from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import SalesAgentError, TenantNotFoundError
from sales_agent.models.base import generate_id
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.answer_service import OntologyAnswerService, render_response_prompt
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.progress import progress_bus
from sales_agent.ontology.repository import OntologyRepository
from sales_agent.ontology.retrieval_service import OntologyRetrievalService
from sales_agent.services.agent_service import AgentService, AgentNotFoundError
from sales_agent.services.tenant_resolver import TenantResolver

router = APIRouter(prefix="/agents", tags=["ontology"])

ALLOWED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf", ".pptx"}

_logger = logging.getLogger(__name__)

# 后台入库任务的强引用集合：asyncio.create_task 返回的 Task 若不保留引用会被 GC 回收，
# 导致任务静默消失、job 永远卡 running。任务完成后由 done 回调自动移除。
_running_ingest_tasks: set = set()

# 单个文件入库的总超时（秒）：不管卡在 LLM / embedding / neo4j / 解析哪一步，
# 超过这个时间就 cancel 并标 job failed，避免永远卡 running。
# 单文件实体抽取 + 分批事实抽取（多次 LLM）正常 1-3 分钟，5 分钟足够兜底。
INGEST_JOB_TIMEOUT_SECONDS = 300


def _on_ingest_task_done(task: asyncio.Task) -> None:
    """后台任务结束：移除引用 + 记录未捕获异常（避免静默失败）。"""
    _running_ingest_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        _logger.error("后台入库任务未捕获异常: %s", exc, exc_info=exc)


async def _load_agent_or_404(agent_id: str, db: DbSession):
    try:
        return await AgentService(db).get_agent(agent_id)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/ontology/status")
async def get_ontology_status(agent_id: str, db: DbSession):
    await _load_agent_or_404(agent_id, db)
    settings = get_settings()
    client = Neo4jClient(settings.neo4j)
    ready = False
    if client.enabled:
        ready, _detail = await client.verify_connectivity()
        await client.close()
    return {
        "knowledge_engine": settings.ontology.knowledge_engine,
        "ontology_status": "ready" if ready else ("not_configured" if not client.enabled else "failed"),
        "neo4j_configured": client.enabled,
        "neo4j_ready": ready,
        "visual_url": settings.neo4j.visual_url,
    }


@router.post("/{agent_id}/ontology/ingest", status_code=202)
async def start_ontology_ingest(
    agent_id: str,
    files: list[UploadFile] = File(...),
    db: DbSession = None,
) -> list[dict[str, Any]]:
    agent = await _load_agent_or_404(agent_id, db)
    settings = get_settings()

    data_dir = Path(settings.app.data_dir or "data")
    dest_dir = data_dir / "agents" / agent.id / "ontology"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for f in files:
        ext = Path(f.filename or "unknown.md").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"仅支持 {ALLOWED_EXTENSIONS} 文件：{f.filename}")

        dest_name = f"{generate_id()}_{f.filename}"
        dest_path = dest_dir / dest_name
        with dest_path.open("wb") as buf:
            shutil.copyfileobj(f.file, buf)

        job = IngestionJob(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            engine="ontology_neo4j",
            status="running",
            stage="uploaded",
            documents_seen=1,
            metadata_json=json.dumps({"filename": f.filename}, ensure_ascii=False),
        )
        db.add(job)
        await db.flush()

        # 保留 Task 强引用，避免被 GC 回收（asyncio.create_task 不保留引用会被回收，
        # 导致后台入库任务静默消失、job 永远卡 running）。任务完成后自动从集合移除。
        task = asyncio.create_task(
            _run_ingest_background(
                job_id=job.id,
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                path=dest_path,
            )
        )
        _running_ingest_tasks.add(task)
        task.add_done_callback(_on_ingest_task_done)
        results.append({"job_id": job.id, "filename": f.filename})

    return results


async def _run_ingest_background(
    job_id: str,
    tenant_id: str,
    agent_id: str,
    path: Path,
) -> None:
    # 整个 body 包进 try：任何阶段（含 session 创建 / TenantResolver / 抽取）失败都把 job 标 failed，
    # 不再静默死（asyncio.create_task 未捕获的异常会被吞，job 永远卡 running）。
    try:
        from sales_agent.services.tenant_resolver import TenantResolver
        from sales_agent.core.database import get_session_factory
        from sales_agent.ontology.runner import build_ingestion_service

        session_factory = get_session_factory()
        async with session_factory() as bg_db:
            resolver = TenantResolver(bg_db)
            tenant_info = await resolver.resolve(tenant_id)
            model_provider = resolver.get_model_provider(tenant_info)

            async def _on_progress(stage: str, stats: dict):
                await progress_bus.publish(job_id, {"stage": stage, "status": "running", "stats": stats})

            settings = get_settings()
            service = build_ingestion_service(bg_db, settings, model_provider)
            job, stats = await asyncio.wait_for(
                service.ingest_paths(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    paths=[path],
                    progress_callback=_on_progress,
                ),
                timeout=INGEST_JOB_TIMEOUT_SECONDS,
            )
            await bg_db.commit()
            await progress_bus.publish(job_id, {
                "stage": job.stage,
                "status": job.status,
                "stats": stats.to_metadata(),
            })
            return  # 成功，直接返回
    except Exception as exc:
        # 超时给个清楚点的提示（asyncio.TimeoutError 的 str() 是空的）
        is_timeout = isinstance(exc, asyncio.TimeoutError)
        err_msg = f"入库超时（>{INGEST_JOB_TIMEOUT_SECONDS}s，可能 LLM/外部服务卡死）" if is_timeout else str(exc)[:500]
        # 用全新 session 标 job failed（原 bg_db 可能已坏或未建立）
        try:
            from sales_agent.core.database import get_session_factory as _gsf
            async with _gsf()() as _db:
                row = (await _db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one_or_none()
                if row:
                    row.status = "failed"
                    row.error_summary = err_msg
                    await _db.commit()
        except Exception:  # noqa: BLE001
            pass
        await progress_bus.publish(job_id, {
            "stage": "failed",
            "status": "failed",
            "error_summary": str(exc)[:500],
            "stats": {},
        })


@router.get("/{agent_id}/ontology/jobs/{job_id}/events")
async def job_events(agent_id: str, job_id: str, db: DbSession):
    await _load_agent_or_404(agent_id, db)

    async def _stream():
        q = progress_bus.subscribe(job_id)
        try:
            # 先推当前快照
            row = (await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one_or_none()
            if row:
                snap = {
                    "stage": row.stage or "uploaded",
                    "status": row.status,
                    "stats": {
                        "entities_created": row.entities_created or 0,
                        "facts_created": row.facts_created or 0,
                        "facts_pending_review": row.facts_pending_review or 0,
                        "conflicts_created": row.conflicts_created or 0,
                    },
                }
                yield f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                stage = event.get("stage", "")
                status = event.get("status", "running")
                event_type = "done" if status in ("completed", "completed_with_errors", "failed") else "progress"
                yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event_type == "done":
                    break
        finally:
            progress_bus.remove(job_id)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/{agent_id}/ontology/jobs")
async def list_ontology_jobs(agent_id: str, db: DbSession, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    agent = await _load_agent_or_404(agent_id, db)
    conds = [IngestionJob.tenant_id == agent.tenant_id, IngestionJob.agent_id == agent.id, IngestionJob.engine == "ontology_neo4j"]
    total = (await db.execute(select(func.count()).select_from(IngestionJob).where(*conds))).scalar() or 0
    rows = (await db.execute(
        select(IngestionJob).where(*conds).order_by(IngestionJob.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return {"items": [_job_to_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _job_to_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "agent_id": job.agent_id,
        "engine": job.engine,
        "status": job.status,
        "stage": job.stage,
        "documents_seen": job.documents_seen,
        "documents_ingested": job.documents_ingested,
        "entities_created": job.entities_created,
        "entities_merged": job.entities_merged,
        "facts_created": job.facts_created,
        "facts_active": job.facts_active,
        "facts_pending_review": job.facts_pending_review,
        "facts_rejected": job.facts_rejected,
        "conflicts_created": job.conflicts_created,
        "warnings": json.loads(job.warnings_json or "[]"),
        "errors": json.loads(job.errors_json or "[]"),
        "error_summary": job.error_summary,
        "metadata": json.loads(job.metadata_json or "{}"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


# ---------------------------------------------------------------------------
# Ontology Explorer（本体探索器）——查询/调试端点
# 复用 OntologyRetrievalService + OntologyAnswerService，把 GraphEvidence 与
# 答案透出给前端三栏调试 UI（检索过程 | 问答 | 完整上下文）。
# ---------------------------------------------------------------------------

EXPLORER_TASK_TYPE = "ontology_qa"


class OntologyQueryRequest(BaseModel):
    """探索器查询请求。history 当前后端未使用，仅与外部项目接口对齐预留。"""

    query: str = Field(..., min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)


async def _build_explorer_services(agent, db: DbSession):
    """构建探索器检索/回答服务。Neo4j 未启用或不可达时抛 503。

    返回 (client, retrieval_service, answer_service)。调用方负责 `await client.close()`。
    """
    settings = get_settings()
    client = Neo4jClient(settings.neo4j)
    if not client.enabled:
        await client.close()
        raise HTTPException(status_code=503, detail="Neo4j 知识图谱未配置，请在实例配置中启用 ontology_neo4j 引擎。")
    try:
        ready, _detail = await client.verify_connectivity()
        if not ready:
            raise HTTPException(status_code=503, detail="Neo4j 无法连接，请检查连接配置。")
        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(agent.tenant_id)
        provider = resolver.get_model_provider(tenant_info)
    except HTTPException:
        await client.close()
        raise
    except (TenantNotFoundError, SalesAgentError) as exc:
        await client.close()
        raise HTTPException(status_code=403, detail=getattr(exc, "user_message", str(exc)))

    repository = OntologyRepository(client)
    retrieval = OntologyRetrievalService(repository, provider.embedding)
    answer_service = OntologyAnswerService(retrieval, provider.chat)
    return client, retrieval, answer_service


def _build_full_context(evidence, query: str) -> dict[str, Any]:
    """组装右栏「完整上下文」：渲染后的 system prompt + 用户问题 + GraphEvidence。"""
    return {
        "system_prompt": render_response_prompt(evidence, query, EXPLORER_TASK_TYPE),
        "user_query": query,
        **evidence.to_dict(),
    }


def _build_search_process(evidence, query: str) -> dict[str, Any]:
    """组装左栏「检索过程」摘要。"""
    return {
        "query": query,
        "strategy": evidence.retrieval_strategy,
        "vector_fallback_used": evidence.vector_fallback_used,
        "confidence": evidence.confidence,
        "matched_entities": evidence.matched_entities,
        "center_entities": evidence.center_entities,
        "facts_used": evidence.facts_used,
        "timings_ms": evidence.timings_ms,
    }


@router.get("/{agent_id}/ontology/stats")
async def ontology_stats(agent_id: str, db: DbSession):
    """探索器头部徽章：按 Entity.type 分组计数。"""
    agent = await _load_agent_or_404(agent_id, db)
    settings = get_settings()
    client = Neo4jClient(settings.neo4j)
    if not client.enabled:
        await client.close()
        raise HTTPException(status_code=503, detail="Neo4j 知识图谱未配置。")
    try:
        counts = await OntologyRepository(client).count_entities_by_type({
            "tenant_id": agent.tenant_id,
            "agent_id": agent.id,
        })
    finally:
        await client.close()
    total = sum(counts.values())
    return {"stats": {"total_entities": total}, "entity_type_counts": counts}


@router.post("/{agent_id}/ontology/query")
async def ontology_query(agent_id: str, req: OntologyQueryRequest, db: DbSession):
    """同步本体查询：返回答案 + 检索过程 + 完整上下文。"""
    agent = await _load_agent_or_404(agent_id, db)
    client, retrieval, answer_service = await _build_explorer_services(agent, db)
    try:
        evidence = await retrieval.retrieve(
            tenant_id=agent.tenant_id, agent_id=agent.id, question=req.query
        )
        result = await answer_service.generate_answer(
            graph_evidence=evidence, message=req.query, task_type=EXPLORER_TASK_TYPE
        )
    finally:
        await client.close()
    return {
        "query": req.query,
        "answer": result.answer,
        "sources": result.sources,
        "search_process": _build_search_process(evidence, req.query),
        "full_context": _build_full_context(evidence, req.query),
    }


@router.post("/{agent_id}/ontology/query/stream")
async def ontology_query_stream(agent_id: str, req: OntologyQueryRequest, db: DbSession):
    """SSE 流式本体查询：step / search_process / result / error 四类事件。

    就绪检查在返回 StreamingResponse 之前完成（503 可正常抛出）；检索/生成阶段
    的异常转为 `error` 事件。
    """
    agent = await _load_agent_or_404(agent_id, db)
    client, retrieval, answer_service = await _build_explorer_services(agent, db)

    def _sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _step(n: int, message: str, status: str) -> str:
        return _sse({"type": "step", "step": n, "message": message, "status": status})

    async def _stream():
        try:
            yield _step(1, "检索知识图谱…", "processing")
            evidence = await retrieval.retrieve(
                tenant_id=agent.tenant_id, agent_id=agent.id, question=req.query
            )
            yield _sse({"type": "search_process", "data": _build_search_process(evidence, req.query)})
            fallback_note = "（向量兜底）" if evidence.vector_fallback_used else ""
            yield _step(2, f"命中 {len(evidence.matched_entities)} 个实体{fallback_note}", "success")
            yield _step(3, "生成回答…", "processing")
            result = await answer_service.generate_answer(
                graph_evidence=evidence, message=req.query, task_type=EXPLORER_TASK_TYPE
            )
            yield _sse({
                "type": "result",
                "answer": result.answer,
                "full_context": _build_full_context(evidence, req.query),
            })
            yield _step(4, "AI 回答生成完成", "success")
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)[:500]})
        finally:
            await client.close()

    return StreamingResponse(_stream(), media_type="text/event-stream")
