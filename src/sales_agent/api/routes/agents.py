"""Agent Instance API 路由。

提供 Agent CRUD、状态转换、克隆、就绪检查，以及对既有 tenant 级数据的
Agent 作用域包装查询。所有 Agent 作用域查询都强制 (tenant_id, agent_id) 双过滤。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import (
    AgentCreateRequest,
    AgentStatusAction,
    AgentUpdateRequest,
    CloneOptions,
)
from sales_agent.core.tenant_runtime import get_tenant_runtime
from sales_agent.models.agent import Agent
from sales_agent.models.agent_channel_config import AgentChannelConfig
from sales_agent.models.agent_clone_manifest import AgentCloneManifest
from sales_agent.models.alert import Alert, AlertRule
from sales_agent.models.conversation import Conversation
from sales_agent.models.document import Document
from sales_agent.models.eval import EvalRun, EvalSuite
from sales_agent.models.feedback import Feedback
from sales_agent.models.knowledge_gap import KnowledgeGap
from sales_agent.models.pilot_report import PilotReport
from sales_agent.models.prompt import PromptVersion
from sales_agent.models.review_item import ReviewItem
from sales_agent.services.agent_clone_service import AgentCloneService, _manifest_to_dict
from sales_agent.services.agent_readiness_service import ReadinessService, can_activate
from sales_agent.services.agent_service import (
    AgentNotFoundError,
    AgentOwnershipError,
    AgentService,
    AgentStatusError,
    _agent_to_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


def _enforce_tenant(agent: Agent) -> None:
    """dedicated 模式下校验 Agent 归属当前实例 tenant。"""
    runtime = get_tenant_runtime()
    if runtime.deployment_mode == "dedicated" and agent.tenant_id != runtime.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AGENT_TENANT_MISMATCH",
                "message": "Agent 不属于当前实例租户",
            },
        )


async def _load_agent(db: AsyncSession, agent_id: str) -> Agent:
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    _enforce_tenant(agent)
    return agent


# ---- CRUD ----


@router.get("")
async def list_agents(
    db: DbSession,
    tenant_id: str | None = Query(None),
    status: str | None = Query(None),
    agent_type: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出 Agent，支持 tenant/status/type/q 过滤。"""
    svc = AgentService(db)
    # dedicated 模式下强制绑定实例 tenant
    runtime = get_tenant_runtime()
    eff_tenant = tenant_id
    if runtime.deployment_mode == "dedicated":
        eff_tenant = runtime.tenant_id
    items, total = await svc.list_agents(
        tenant_id=eff_tenant, status=status, agent_type=agent_type, q=q,
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=201)
async def create_agent(req: AgentCreateRequest, db: DbSession):
    """创建 draft Agent。dedicated 模式下自动用实例租户，无需前端选租户。"""
    runtime = get_tenant_runtime()
    payload = req.model_dump()
    if runtime.deployment_mode == "dedicated":
        payload["tenant_id"] = runtime.tenant_id  # 强制绑定实例租户
    svc = AgentService(db)
    agent = await svc.create_agent(payload)
    return agent


@router.get("/{agent_id}")
async def get_agent(agent_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    return _agent_to_dict(agent)


@router.patch("/{agent_id}")
async def update_agent(agent_id: str, req: AgentUpdateRequest, db: DbSession):
    agent = await _load_agent(db, agent_id)
    svc = AgentService(db)
    try:
        return await svc.update_agent(agent_id, req.model_dump(exclude_unset=True))
    except AgentStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---- 状态转换 ----


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: str, db: DbSession, body: AgentStatusAction | None = None):
    """激活 Agent：必须通过就绪检查或携带豁免理由。"""
    agent = await _load_agent(db, agent_id)
    # 记录豁免
    if body and body.waiver_reasons:
        flags = json.loads(agent.feature_flags_json or "{}")
        waivers = flags.setdefault("activation_waivers", {})
        waivers.update(body.waiver_reasons)
        agent.feature_flags_json = json.dumps(flags, ensure_ascii=False)
        await db.flush()

    ready, report = await can_activate(db, agent)
    if not ready:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOT_READY",
                "message": "Agent 未通过就绪检查",
                "blockers": report["blockers"],
                "checks": report["checks"],
            },
        )
    svc = AgentService(db)
    try:
        return await svc.transition_status(agent_id, "active")
    except AgentStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{agent_id}/pause")
async def pause_agent(agent_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    svc = AgentService(db)
    try:
        return await svc.transition_status(agent_id, "paused")
    except AgentStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{agent_id}/archive")
async def archive_agent(agent_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    svc = AgentService(db)
    try:
        return await svc.transition_status(agent_id, "archived")
    except AgentStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---- 克隆 ----


@router.post("/{agent_id}/clone", status_code=201)
async def clone_agent(agent_id: str, req: CloneOptions, db: DbSession):
    """从既有 Agent 克隆出一个 draft Agent。"""
    source = await _load_agent(db, agent_id)
    # 跨 tenant 克隆目标也须属于实例 tenant（dedicated）
    runtime = get_tenant_runtime()
    target_tenant = req.tenant_id or source.tenant_id
    if runtime.deployment_mode == "dedicated" and target_tenant != runtime.tenant_id:
        raise HTTPException(status_code=403, detail={"code": "AGENT_TENANT_MISMATCH"})

    svc = AgentCloneService(db)
    result = await svc.clone(source.id, req.model_dump())
    return result


@router.get("/{agent_id}/clone-manifest")
async def get_clone_manifest(agent_id: str, db: DbSession):
    """获取该 Agent 作为克隆目标的最新 manifest。"""
    await _load_agent(db, agent_id)
    row = (
        await db.execute(
            select(AgentCloneManifest)
            .where(AgentCloneManifest.target_agent_id == agent_id)
            .order_by(AgentCloneManifest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No clone manifest for this agent")
    return _manifest_to_dict(row)


# ---- 就绪 ----


@router.get("/{agent_id}/readiness")
async def get_readiness(agent_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    return await ReadinessService(db).evaluate(agent)


# ---- Agent 作用域数据包装 ----


@router.get("/{agent_id}/conversations")
async def list_agent_conversations(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出该 Agent 的会话（强制 agent_id 过滤）。"""
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, Conversation, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "user_id", "channel",
                                        "message", "task_type", "status", "created_at"))


@router.get("/{agent_id}/feedback")
async def list_agent_feedback(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, Feedback, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "conversation_id",
                                        "user_id", "rating", "review_status", "created_at"))


@router.get("/{agent_id}/knowledge/documents")
async def list_agent_documents(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出该 Agent 知识作用域内的文档。"""
    from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope
    agent = await _load_agent(db, agent_id)
    if not agent.knowledge_scope_id:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    scope = (
        await db.execute(
            select(AgentKnowledgeScope).where(AgentKnowledgeScope.id == agent.knowledge_scope_id)
        )
    ).scalar_one_or_none()
    if scope is None or scope.mode == "tenant_all":
        # 全量 tenant 文档
        return await _paginated(db, Document, agent, limit, offset,
                                extra_cols=("id", "tenant_id", "title", "status", "created_at"))
    # 子集过滤
    doc_ids = json.loads(scope.document_ids_json or "[]")
    conds = [Document.tenant_id == agent.tenant_id, Document.agent_id == agent.id]
    if scope.mode == "document_subset" and doc_ids:
        conds.append(Document.id.in_(doc_ids))
    count_stmt = select(func.count()).select_from(Document).where(*conds)
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = (select(Document).where(*conds).order_by(Document.created_at.desc())
            .limit(limit).offset(offset))
    rows = (await db.execute(stmt)).scalars().all()
    items = [{"id": d.id, "title": d.title, "status": d.status, "created_at": d.created_at}
             for d in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{agent_id}/prompts")
async def list_agent_prompts(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出该 Agent 的 prompt 版本（含租户级 active + 该 Agent 独立副本）。"""
    from sales_agent.models.agent_prompt_set import AgentPromptSet
    from sqlalchemy import or_
    agent = await _load_agent(db, agent_id)
    # 独立副本 OR 该 tenant 的 active 版本
    where = [PromptVersion.tenant_id == agent.tenant_id,
             or_(PromptVersion.agent_id == agent.id,
                 (PromptVersion.agent_id.is_(None)) & (PromptVersion.status == "active"))]
    total = (await db.execute(
        select(func.count()).select_from(PromptVersion).where(*where)
    )).scalar() or 0
    rows = (await db.execute(
        select(PromptVersion).where(*where).order_by(PromptVersion.created_at.desc())
        .limit(limit).offset(offset)
    )).scalars().all()
    items = [{
        "id": r.id, "task_type": r.task_type, "version": r.version, "status": r.status,
        "description": r.description, "agent_scoped": r.agent_id == agent.id,
        "created_at": r.created_at, "updated_at": r.updated_at,
    } for r in rows]
    # 附带 Agent prompt_set 映射
    ps = (await db.execute(
        select(AgentPromptSet).where(AgentPromptSet.id == agent.prompt_set_id)
    )).scalar_one_or_none() if agent.prompt_set_id else None
    mapping = json.loads(ps.task_prompt_versions_json or "{}") if ps else {}
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "prompt_set_mapping": mapping}


@router.get("/{agent_id}/channels")
async def list_agent_channels(agent_id: str, db: DbSession):
    """列出该 Agent 的渠道配置（不含明文密钥）。"""
    agent = await _load_agent(db, agent_id)
    rows = (await db.execute(
        select(AgentChannelConfig).where(AgentChannelConfig.agent_id == agent.id)
        .order_by(AgentChannelConfig.created_at.desc())
    )).scalars().all()
    items = [{
        "id": r.id, "channel": r.channel, "status": r.status,
        "config": json.loads(r.config_json or "{}"),
        # secret_refs 仅返回 key 名，不返回任何值
        "secret_ref_keys": list(json.loads(r.secret_refs_json or "{}").keys()),
        "created_at": r.created_at, "updated_at": r.updated_at,
    } for r in rows]
    return {"items": items, "total": len(items)}


@router.get("/{agent_id}/alerts")
async def list_agent_alerts(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, Alert, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "alert_rule_id", "severity",
                                        "metric", "status", "observed_value", "last_seen_at",
                                        "created_at"))


@router.get("/{agent_id}/reports")
async def list_agent_reports(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, PilotReport, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "report_type", "start_date",
                                        "end_date", "status", "created_at", "updated_at"))


@router.get("/{agent_id}/eval-runs")
async def list_agent_eval_runs(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, EvalRun, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "eval_suite_id", "status",
                                        "total_cases", "passed", "failed", "skipped",
                                        "started_at", "completed_at", "created_at"))


@router.get("/{agent_id}/review-queue")
async def list_agent_review_queue(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, ReviewItem, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "conversation_id", "reason",
                                        "priority", "status", "assignee", "created_at"))


@router.get("/{agent_id}/knowledge-gaps")
async def list_agent_knowledge_gaps(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    return await _paginated(db, KnowledgeGap, agent, limit, offset,
                            extra_cols=("id", "tenant_id", "title", "status", "priority",
                                        "linked_document_id", "created_at", "updated_at"))


async def _paginated(db: AsyncSession, model, agent: Agent, limit: int, offset: int, extra_cols):
    """对任意带 tenant_id + agent_id 的模型做 Agent 作用域分页查询。"""
    conds = [model.tenant_id == agent.tenant_id, model.agent_id == agent.id]
    count_stmt = select(func.count()).select_from(model).where(*conds)
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = (select(model).where(*conds).order_by(model.created_at.desc())
            .limit(limit).offset(offset))
    rows = (await db.execute(stmt)).scalars().all()
    items = []
    for r in rows:
        item: dict[str, Any] = {}
        for col in extra_cols:
            item[col] = getattr(r, col, None)
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
