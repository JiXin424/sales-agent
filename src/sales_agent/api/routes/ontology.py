from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.models.base import generate_id
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.progress import progress_bus
from sales_agent.services.agent_service import AgentService, AgentNotFoundError

router = APIRouter(prefix="/agents", tags=["ontology"])

ALLOWED_EXTENSIONS = {".md", ".txt"}


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
        )
        db.add(job)
        await db.flush()

        asyncio.create_task(
            _run_ingest_background(
                job_id=job.id,
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                path=dest_path,
            )
        )
        results.append({"job_id": job.id, "filename": f.filename})

    return results


async def _run_ingest_background(
    job_id: str,
    tenant_id: str,
    agent_id: str,
    path: Path,
) -> None:
    from sales_agent.services.tenant_resolver import TenantResolver
    from sales_agent.core.database import get_session_factory
    from sqlalchemy.ext.asyncio import AsyncSession

    # 后台任务需要独立 session（不能使用请求的 db session，请求结束后会被关闭）
    session_factory = get_session_factory()
    async with session_factory() as bg_db:
        try:
            resolver = TenantResolver(bg_db)
            tenant_info = await resolver.resolve(tenant_id)
            model_provider = resolver.get_model_provider(tenant_info)

            from sales_agent.ontology.runner import build_ingestion_service

            async def _on_progress(stage: str, stats: dict):
                await progress_bus.publish(job_id, {"stage": stage, "status": "running", "stats": stats})

            settings = get_settings()
            service = build_ingestion_service(bg_db, settings, model_provider)
            job, stats = await service.ingest_paths(
                tenant_id=tenant_id,
                agent_id=agent_id,
                paths=[path],
                progress_callback=_on_progress,
            )
            # 更新 job 统计（ingestion_service 已 flush 到 bg_db）
            await bg_db.commit()
            await progress_bus.publish(job_id, {
                "stage": job.stage,
                "status": job.status,
                "stats": stats.to_metadata(),
            })
        except Exception as exc:
            # job 行在 bg_db session 里；查出来标 failed
            row = (await bg_db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_summary = str(exc)[:500]
                await bg_db.flush()
                await bg_db.commit()
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
