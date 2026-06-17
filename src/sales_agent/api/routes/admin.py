"""Admin 监控 API：会话、运行追踪、文档、反馈、延迟、模型调用。"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.models.agent_run import AgentRun, AgentRunStep
from sales_agent.models.conversation import Conversation, ConversationMessage
from sales_agent.models.document import Document, SourceFile
from sales_agent.models.feedback import Feedback
from sales_agent.models.model_call_log import ModelCallLog
from sales_agent.services.feedback_service import FeedbackService
from sales_agent.services.latency_stats import get_latency_stats_collector
from sales_agent.services.task_router import WORKFLOW_TASK_TYPES, VISIT_PREPARATION, POST_VISIT_REVIEW
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant_id}/admin", tags=["admin"])


async def _verify_tenant(tenant_id: str, db: AsyncSession) -> None:
    """校验租户存在。"""
    resolver = TenantResolver(db)
    try:
        await resolver.resolve(tenant_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")


# --- Conversations ---


@router.get("/conversations")
async def list_conversations(
    tenant_id: str,
    db: DbSession,
    user_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的会话。"""
    await _verify_tenant(tenant_id, db)
    conditions = [Conversation.tenant_id == tenant_id]
    if user_id:
        conditions.append(Conversation.user_id == user_id)
    if status:
        conditions.append(Conversation.status == status)

    count_stmt = select(func.count()).select_from(Conversation).where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(Conversation)
        .where(*conditions)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for c in rows:
        items.append({
            "id": c.id,
            "tenant_id": c.tenant_id,
            "user_id": c.user_id,
            "channel": c.channel,
            "message": c.message,
            "task_type": c.task_type,
            "task_confidence": c.task_confidence,
            "status": c.status,
            "risk": json.loads(c.risk_json) if c.risk_json else None,
            "sources": json.loads(c.sources_json) if c.sources_json else [],
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        })
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    tenant_id: str,
    conversation_id: str,
    db: DbSession,
):
    """获取会话详情（含消息列表）。"""
    await _verify_tenant(tenant_id, db)

    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.tenant_id == tenant_id,
        )
        .order_by(ConversationMessage.created_at.asc())
    )
    messages = (await db.execute(msg_stmt)).scalars().all()

    return {
        "id": conv.id,
        "tenant_id": conv.tenant_id,
        "user_id": conv.user_id,
        "channel": conv.channel,
        "message": conv.message,
        "task_type": conv.task_type,
        "task_confidence": conv.task_confidence,
        "answer": conv.answer,
        "status": conv.status,
        "risk": json.loads(conv.risk_json) if conv.risk_json else None,
        "sources": json.loads(conv.sources_json) if conv.sources_json else [],
        "error": json.loads(conv.error_json) if conv.error_json else None,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in messages
        ],
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


# --- Run Traces ---


@router.get("/runs/{run_id}")
async def get_run_detail(
    tenant_id: str,
    run_id: str,
    db: DbSession,
):
    """获取 Agent 运行详情。"""
    await _verify_tenant(tenant_id, db)

    stmt = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.tenant_id == tenant_id,
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return {
        "id": run.id,
        "tenant_id": run.tenant_id,
        "conversation_id": run.conversation_id,
        "user_id": run.user_id,
        "task_type": run.task_type,
        "path": run.path,
        "status": run.status,
        "total_latency_ms": run.total_latency_ms,
        "route_confidence": run.route_confidence,
        "error": json.loads(run.error_json) if run.error_json else None,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


@router.get("/runs/{run_id}/steps")
async def get_run_steps(
    tenant_id: str,
    run_id: str,
    db: DbSession,
):
    """获取 Agent 运行的步骤列表。"""
    await _verify_tenant(tenant_id, db)

    # 校验 run 存在且属于租户
    run_stmt = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.tenant_id == tenant_id,
    )
    run = (await db.execute(run_stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    step_stmt = (
        select(AgentRunStep)
        .where(AgentRunStep.agent_run_id == run_id)
        .order_by(AgentRunStep.step_order.asc())
    )
    steps = (await db.execute(step_stmt)).scalars().all()

    return {
        "run_id": run_id,
        "steps": [
            {
                "id": s.id,
                "step_name": s.step_name,
                "step_order": s.step_order,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "metadata": json.loads(s.metadata_json) if s.metadata_json else {},
                "error_summary": s.error_summary,
                "created_at": s.created_at,
            }
            for s in steps
        ],
    }


# --- Documents & Source Files ---


@router.get("/documents")
async def list_documents(
    tenant_id: str,
    db: DbSession,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的文档。"""
    await _verify_tenant(tenant_id, db)
    conditions = [Document.tenant_id == tenant_id]
    if status:
        conditions.append(Document.status == status)

    total = (await db.execute(select(func.count()).select_from(Document).where(*conditions))).scalar() or 0
    rows = (
        await db.execute(
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return {
        "items": [
            {
                "id": d.id,
                "tenant_id": d.tenant_id,
                "title": d.title,
                "source_path": d.source_path,
                "status": d.status,
                "metadata": json.loads(d.metadata_json) if d.metadata_json else {},
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/documents/{document_id}")
async def get_document_detail(
    tenant_id: str,
    document_id: str,
    db: DbSession,
):
    """获取文档详情。"""
    await _verify_tenant(tenant_id, db)
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": doc.id,
        "tenant_id": doc.tenant_id,
        "title": doc.title,
        "source_path": doc.source_path,
        "status": doc.status,
        "metadata": json.loads(doc.metadata_json) if doc.metadata_json else {},
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


@router.get("/source-files")
async def list_source_files(
    tenant_id: str,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的源文件。"""
    await _verify_tenant(tenant_id, db)

    conditions = [SourceFile.tenant_id == tenant_id]
    total = (await db.execute(select(func.count()).select_from(SourceFile).where(*conditions))).scalar() or 0
    rows = (
        await db.execute(
            select(SourceFile)
            .where(*conditions)
            .order_by(SourceFile.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return {
        "items": [
            {
                "id": sf.id,
                "tenant_id": sf.tenant_id,
                "original_filename": sf.original_filename,
                "stored_path": sf.stored_path,
                "content_hash": sf.content_hash,
                "mime_type": sf.mime_type,
                "status": sf.status,
                "created_at": sf.created_at,
                "updated_at": sf.updated_at,
            }
            for sf in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --- Feedback Summary ---


@router.get("/feedback/summary")
async def get_feedback_summary(
    tenant_id: str,
    db: DbSession,
):
    """获取租户反馈汇总。"""
    await _verify_tenant(tenant_id, db)
    svc = FeedbackService(db)
    return await svc.get_summary(tenant_id)


# --- Latency Stats ---


@router.get("/latency-stats")
async def get_latency_stats(
    tenant_id: str,
    db: DbSession,
):
    """获取延迟统计。"""
    await _verify_tenant(tenant_id, db)
    collector = get_latency_stats_collector()
    return collector.get_summary()


# --- Model Call Logs ---


@router.get("/model-calls")
async def list_model_calls(
    tenant_id: str,
    db: DbSession,
    request_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出模型调用日志。"""
    await _verify_tenant(tenant_id, db)
    conditions = [ModelCallLog.tenant_id == tenant_id]
    if request_type:
        conditions.append(ModelCallLog.request_type == request_type)
    if status:
        conditions.append(ModelCallLog.status == status)

    total = (await db.execute(select(func.count()).select_from(ModelCallLog).where(*conditions))).scalar() or 0
    rows = (
        await db.execute(
            select(ModelCallLog)
            .where(*conditions)
            .order_by(ModelCallLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return {
        "items": [
            {
                "id": mc.id,
                "tenant_id": mc.tenant_id,
                "provider": mc.provider,
                "base_url_host": mc.base_url_host,
                "chat_model": mc.chat_model,
                "embedding_model": mc.embedding_model,
                "request_type": mc.request_type,
                "status": mc.status,
                "latency_ms": mc.latency_ms,
                "error_code": mc.error_code,
                "created_at": mc.created_at,
            }
            for mc in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/model-calls/summary")
async def get_model_calls_summary(
    tenant_id: str,
    db: DbSession,
):
    """获取模型调用汇总。"""
    await _verify_tenant(tenant_id, db)

    # 按 provider + status 聚合
    stmt = (
        select(
            ModelCallLog.provider,
            ModelCallLog.status,
            func.count().label("count"),
            func.avg(ModelCallLog.latency_ms).label("avg_latency_ms"),
        )
        .where(ModelCallLog.tenant_id == tenant_id)
        .group_by(ModelCallLog.provider, ModelCallLog.status)
    )
    rows = (await db.execute(stmt)).all()

    summary = {}
    for provider, status, count, avg_latency in rows:
        if provider not in summary:
            summary[provider] = {}
        summary[provider][status] = {
            "count": count,
            "avg_latency_ms": round(avg_latency, 2) if avg_latency else None,
        }
    return summary


# --- Workflow Metrics (Phase B) ---


@router.get("/workflow-metrics")
async def get_workflow_metrics(
    tenant_id: str,
    db: DbSession,
    start_date: str | None = Query(None, description="ISO date, e.g. 2026-01-01"),
    end_date: str | None = Query(None, description="ISO date, e.g. 2026-12-31"),
):
    """获取工作流任务的质量指标（Phase B）。

    返回：
    - task_type_distribution: 工作流任务类型分布
    - stage_distribution: 销售阶段分布
    - low_score_conversations: 低分对话统计
    - feedback_by_workflow_task: 工作流任务的反馈统计
    - common_missing_fields: 常见缺失客户信息字段
    """
    await _verify_tenant(tenant_id, db)

    workflow_types = list(WORKFLOW_TASK_TYPES)
    base_conditions = [Conversation.tenant_id == tenant_id]
    date_conditions: list[Any] = []

    if start_date:
        date_conditions.append(Conversation.created_at >= start_date)
    if end_date:
        date_conditions.append(Conversation.created_at <= end_date + "T23:59:59")

    all_conditions = base_conditions + date_conditions
    workflow_conditions = all_conditions + [Conversation.task_type.in_(workflow_types)]

    # 1. Task type distribution
    task_dist_stmt = (
        select(Conversation.task_type, func.count().label("count"))
        .where(*workflow_conditions)
        .group_by(Conversation.task_type)
    )
    task_dist_rows = (await db.execute(task_dist_stmt)).all()
    task_type_distribution = {row[0]: row[1] for row in task_dist_rows}

    # 2. Stage distribution (from conversations that have a stage value)
    stage_conditions = workflow_conditions + [Conversation.stage.isnot(None)]
    stage_dist_stmt = (
        select(Conversation.stage, func.count().label("count"))
        .where(*stage_conditions)
        .group_by(Conversation.stage)
    )
    stage_dist_rows = (await db.execute(stage_dist_stmt)).all()
    stage_distribution = {row[0]: row[1] for row in stage_dist_rows}

    # 3. Low-score conversations (conversation_scoring with overall score < 60)
    scoring_conditions = all_conditions + [
        Conversation.task_type == "conversation_scoring",
    ]
    scoring_stmt = select(Conversation.id, Conversation.answer).where(
        *scoring_conditions
    )
    scoring_rows = (await db.execute(scoring_stmt)).all()

    low_score_conversations: list[dict[str, Any]] = []
    for conv_id, answer_text in scoring_rows:
        if not answer_text:
            continue
        try:
            answer_data = json.loads(answer_text) if isinstance(answer_text, str) else answer_text
            # Look for overall score in sections
            overall_score = _extract_overall_score(answer_data)
            if overall_score is not None and overall_score < 60:
                low_score_conversations.append({
                    "conversation_id": conv_id,
                    "overall_score": overall_score,
                })
        except (json.JSONDecodeError, TypeError):
            continue

    # 4. Feedback by workflow task
    feedback_stmt = (
        select(
            Conversation.task_type,
            Feedback.rating,
            func.count().label("count"),
        )
        .join(Feedback, Feedback.conversation_id == Conversation.id)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.task_type.in_(workflow_types),
        )
    )
    if date_conditions:
        feedback_stmt = feedback_stmt.where(*date_conditions)
    feedback_stmt = feedback_stmt.group_by(Conversation.task_type, Feedback.rating)

    feedback_rows = (await db.execute(feedback_stmt)).all()
    feedback_by_workflow_task: dict[str, dict[str, int]] = {}
    for task_type, rating, count in feedback_rows:
        if task_type not in feedback_by_workflow_task:
            feedback_by_workflow_task[task_type] = {}
        feedback_by_workflow_task[task_type][rating] = count

    # 5. Common missing fields (from customer_context_summary answers)
    ctx_conditions = all_conditions + [
        Conversation.task_type == "customer_context_summary",
    ]
    ctx_stmt = select(Conversation.answer).where(*ctx_conditions)
    ctx_rows = (await db.execute(ctx_stmt)).all()

    missing_field_counts: dict[str, int] = {}
    for (answer_text,) in ctx_rows:
        if not answer_text:
            continue
        try:
            answer_data = json.loads(answer_text) if isinstance(answer_text, str) else answer_text
            missing_items = _extract_missing_fields(answer_data)
            for item in missing_items:
                missing_field_counts[item] = missing_field_counts.get(item, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue

    # Sort by frequency, keep top 10
    common_missing_fields = sorted(
        missing_field_counts.items(), key=lambda x: x[1], reverse=True
    )[:10]

    # 6. Card adoption metrics (访前/访后卡片使用情况)
    card_types = [VISIT_PREPARATION, POST_VISIT_REVIEW]
    card_conditions = all_conditions + [Conversation.task_type.in_(card_types)]
    card_dist_stmt = (
        select(Conversation.task_type, func.count().label("count"))
        .where(*card_conditions)
        .group_by(Conversation.task_type)
    )
    card_dist_rows = (await db.execute(card_dist_stmt)).all()
    card_adoption = {
        "total_cards": sum(row[1] for row in card_dist_rows),
        "pre_visit_count": next((row[1] for row in card_dist_rows if row[0] == VISIT_PREPARATION), 0),
        "post_visit_count": next((row[1] for row in card_dist_rows if row[0] == POST_VISIT_REVIEW), 0),
    }

    # 7. Opportunity quality indicators (from card outputs)
    opportunity_status_counts: dict[str, int] = {"A可推进": 0, "B需培育": 0, "C高风险": 0, "D暂缓": 0}
    blocker_counts: dict[str, int] = {}
    manager_intervention_count = 0

    for card_type in card_types:
        card_answer_stmt = select(Conversation.answer).where(
            *(all_conditions + [Conversation.task_type == card_type])
        )
        card_rows = (await db.execute(card_answer_stmt)).all()
        for (answer_text,) in card_rows:
            if not answer_text:
                continue
            try:
                data = json.loads(answer_text) if isinstance(answer_text, str) else answer_text
                sections = data.get("sections", [])
                for section in sections:
                    title = section.get("title", "")
                    content = section.get("content", "")

                    # 机会状态分布
                    if "机会状态" in title:
                        for status_key in opportunity_status_counts:
                            if status_key in content:
                                opportunity_status_counts[status_key] += 1

                    # 卡点分布
                    if "最大卡点" in title or "卡点" in title:
                        # 取前 50 字作为卡点摘要
                        blocker_summary = content[:50].strip()
                        if blocker_summary:
                            blocker_counts[blocker_summary] = blocker_counts.get(blocker_summary, 0) + 1

                    # 管理者介入计数
                    if "管理者介入" in title:
                        if "需要" in content and "不需要" not in content:
                            manager_intervention_count += 1

            except (json.JSONDecodeError, TypeError):
                continue

    return {
        "task_type_distribution": task_type_distribution,
        "stage_distribution": stage_distribution,
        "low_score_conversations": low_score_conversations,
        "feedback_by_workflow_task": feedback_by_workflow_task,
        "common_missing_fields": [
            {"field": field, "count": count}
            for field, count in common_missing_fields
        ],
        "card_adoption": card_adoption,
        "opportunity_status_distribution": opportunity_status_counts,
        "blocker_distribution": [
            {"blocker": blocker, "count": count}
            for blocker, count in sorted(blocker_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ],
        "manager_intervention_count": manager_intervention_count,
    }


def _extract_overall_score(answer_data: dict[str, Any]) -> int | None:
    """从 conversation_scoring 输出中提取总分。"""
    sections = answer_data.get("sections", [])
    for section in sections:
        title = section.get("title", "")
        if "总分" in title:
            content = section.get("content", "")
            # Extract first number from content like "72/100（加权计算）"
            import re
            match = re.search(r"(\d+)\s*/\s*100", content)
            if match:
                return int(match.group(1))
            match = re.search(r"(\d+)", content)
            if match:
                return int(match.group(1))
    return None


def _extract_missing_fields(answer_data: dict[str, Any]) -> list[str]:
    """从 customer_context_summary 输出中提取缺失信息字段。"""
    sections = answer_data.get("sections", [])
    for section in sections:
        title = section.get("title", "")
        if "缺失信息" in title:
            content = section.get("content", "")
            # Extract numbered items like "1. 预算范围" or "- 预算范围"
            import re
            items = re.findall(r"(?:\d+\.\s*|[-•]\s*)(.+?)(?:\n|$)", content)
            return [item.strip() for item in items if item.strip()]
    return []
