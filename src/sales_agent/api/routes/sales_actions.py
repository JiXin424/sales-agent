"""销售任务（sales actions）运维 API - 只读列表/详情 + 测试/内部工具变更。

路由前缀 ``/agents``，与既有 agent 作用域路由（``agents.py``）保持一致；
端点挂在 ``/{agent_id}/sales-actions`` 下。运维视角按 (tenant, agent) 聚合
（跨用户），user_id 作为可选过滤项；变更端点（complete/cancel/snooze）复用
仓储幂等状态机，与钉钉卡片回调路径走同一套保证。

注：计划文档写的是 ``/api/v1/agents/{agent_id}/sales-actions``，但本仓库路由
无 ``/api/v1`` 全局前缀（见 ``main.py`` 的 ``include_router`` 与前端 ``client.ts``
的 BASE_URL 为空），故采用 ``/agents/...`` 以与既有约定一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sales_agent.api.deps import DbSession
from sales_agent.api.routes.agents import _load_agent
from sales_agent.services.sales_actions.contracts import SalesActionScope
from sales_agent.services.sales_actions.repository import (
    SalesActionRepository,
    SalesActionReminder,
    SalesActionDelivery,
    SalesActionEvent,
    SalesActionCard,
)

router = APIRouter(prefix="/agents", tags=["sales-actions"])


# ---------------------------------------------------------------------------
# response schemas
# ---------------------------------------------------------------------------


class SalesActionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    agent_id: str
    user_id: str
    channel: str
    dingtalk_user_id: str | None = None
    conversation_id: str
    title: str
    customer_name: str | None = None
    action_type: str
    scheduled_at: datetime
    timezone: str
    status: str
    priority: str
    source_kind: str
    created_at: str


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_id: str | None = None
    user_id: str
    remind_at: datetime
    reminder_type: str
    status: str
    attempts: int
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    created_at: str


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_id: str | None = None
    reminder_id: str | None = None
    user_id: str
    channel: str
    delivery_type: str
    dingtalk_message_id: str | None = None
    card_instance_id: str | None = None
    rendered_text: str = ""
    status: str
    error: str | None = None
    created_at: str


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_id: str | None = None
    user_id: str
    event_type: str
    created_at: str


class SalesActionDetail(BaseModel):
    card: SalesActionSummary
    reminders: list[ReminderOut]
    deliveries: list[DeliveryOut]
    events: list[EventOut]


class SalesActionListResponse(BaseModel):
    items: list[SalesActionSummary]
    total: int


class ReminderListResponse(BaseModel):
    items: list[ReminderOut]
    total: int


class DeliveryListResponse(BaseModel):
    items: list[DeliveryOut]
    total: int


class CreateSalesActionRequest(BaseModel):
    """测试/内部工具用：直接落一张动作卡片（不走 LLM 解析）。"""

    title: str
    customer_name: str | None = None
    action_type: str = "other"
    scheduled_at: datetime
    timezone: str = "Asia/Shanghai"
    user_id: str
    dingtalk_user_id: str | None = None
    conversation_id: str
    topic_id: str | None = None
    source_event_id: str | None = None
    source_kind: str = "manual_ops"
    agent_advice: str = ""
    context_snapshot: dict[str, Any] = Field(default_factory=dict)


class SnoozeRequest(BaseModel):
    new_time: datetime
    event_id: str | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _summary(card: SalesActionCard) -> SalesActionSummary:
    return SalesActionSummary.model_validate(card)


async def _resolve_scope(
    repo: SalesActionRepository, tenant_id: str, agent_id: str, action_id: str
) -> SalesActionScope:
    """按 (tenant, agent, action_id) 定位卡片，反推其 (user, dingtalk_user) 作用域。

    变更端点复用仓储状态机，故需要与卡片一致的 ``SalesActionScope``。
    """
    detail = await repo.get_action_detail(tenant_id, agent_id, action_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Sales action not found: {action_id}")
    card: SalesActionCard = detail["card"]
    return SalesActionScope(
        tenant_id=card.tenant_id,
        agent_id=card.agent_id,
        user_id=card.user_id,
        channel=card.channel,
        dingtalk_user_id=card.dingtalk_user_id,
    )


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get("/{agent_id}/sales-actions")
async def list_sales_actions(
    agent_id: str,
    db: DbSession,
    status: str | None = None,
    action_type: str | None = None,
    user_id: str | None = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
) -> SalesActionListResponse:
    """列出该 Agent 下的销售任务（跨用户，可按状态/类型/用户/时间过滤）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    cards = await repo.list_actions_for_agent(
        agent.tenant_id,
        agent_id,
        status=status,
        action_type=action_type,
        user_id=user_id,
        scheduled_from=scheduled_from,
        scheduled_to=scheduled_to,
    )
    return SalesActionListResponse(items=[_summary(c) for c in cards], total=len(cards))


@router.post("/{agent_id}/sales-actions", status_code=201)
async def create_sales_action(
    agent_id: str,
    req: CreateSalesActionRequest,
    db: DbSession,
) -> SalesActionSummary:
    """测试/内部工具：直接创建一张动作卡片 + 一条 one_time 提醒。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    scope = SalesActionScope(
        tenant_id=agent.tenant_id,
        agent_id=agent_id,
        user_id=req.user_id,
        channel="dingtalk",
        dingtalk_user_id=req.dingtalk_user_id,
    )
    card = await repo.create_action(
        scope,
        title=req.title,
        customer_name=req.customer_name,
        action_type=req.action_type,
        scheduled_at=req.scheduled_at,
        timezone=req.timezone,
        conversation_id=req.conversation_id,
        topic_id=req.topic_id,
        source_event_id=req.source_event_id,
        source_kind=req.source_kind,
        context_snapshot=req.context_snapshot,
        agent_advice=req.agent_advice,
    )
    await db.commit()
    return _summary(card)


@router.get("/{agent_id}/sales-actions/{action_id}")
async def get_sales_action(agent_id: str, action_id: str, db: DbSession) -> SalesActionDetail:
    """详情：动作卡片 + 其提醒/投递/事件。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    detail = await repo.get_action_detail(agent.tenant_id, agent_id, action_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Sales action not found: {action_id}")
    return SalesActionDetail(
        card=_summary(detail["card"]),
        reminders=[ReminderOut.model_validate(r) for r in detail["reminders"]],
        deliveries=[DeliveryOut.model_validate(d) for d in detail["deliveries"]],
        events=[EventOut.model_validate(e) for e in detail["events"]],
    )


@router.post("/{agent_id}/sales-actions/{action_id}/complete")
async def complete_sales_action(agent_id: str, action_id: str, db: DbSession) -> dict:
    """标记完成（幂等，复用状态机；与钉钉回调同路径）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    scope = await _resolve_scope(repo, agent.tenant_id, agent_id, action_id)
    result = await repo.complete_action(scope, action_id)
    await db.commit()
    return {"action_id": result.action_id, "status": result.status, "reason_code": result.reason_code}


@router.post("/{agent_id}/sales-actions/{action_id}/cancel")
async def cancel_sales_action(agent_id: str, action_id: str, db: DbSession) -> dict:
    """取消（幂等）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    scope = await _resolve_scope(repo, agent.tenant_id, agent_id, action_id)
    result = await repo.cancel_action(scope, action_id)
    await db.commit()
    return {"action_id": result.action_id, "status": result.status, "reason_code": result.reason_code}


@router.post("/{agent_id}/sales-actions/{action_id}/snooze")
async def snooze_sales_action(
    agent_id: str, action_id: str, req: SnoozeRequest, db: DbSession
) -> dict:
    """推迟到 new_time（幂等）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    scope = await _resolve_scope(repo, agent.tenant_id, agent_id, action_id)
    outcome = await repo.snooze_action(
        scope, action_id, event_id=req.event_id, new_time=req.new_time
    )
    await db.commit()
    # snooze_action returns SalesActionReminder (fresh) | ActionStateResult (idempotent)
    if isinstance(outcome, SalesActionReminder):
        return {"action_id": action_id, "status": "snoozed", "reason_code": "snoozed"}
    return {
        "action_id": outcome.action_id,
        "status": outcome.status,
        "reason_code": outcome.reason_code,
    }


@router.get("/{agent_id}/sales-actions/reminders")
async def list_sales_action_reminders(
    agent_id: str,
    db: DbSession,
    status: str | None = None,
    user_id: str | None = None,
) -> ReminderListResponse:
    """列出该 Agent 下的提醒（可按状态/用户过滤，含 failed/sent）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    rows = await repo.list_reminders_for_agent(
        agent.tenant_id, agent_id, status=status, user_id=user_id
    )
    return ReminderListResponse(
        items=[ReminderOut.model_validate(r) for r in rows], total=len(rows)
    )


@router.get("/{agent_id}/sales-actions/deliveries")
async def list_sales_action_deliveries(
    agent_id: str,
    db: DbSession,
    status: str | None = None,
    user_id: str | None = None,
) -> DeliveryListResponse:
    """列出该 Agent 下的投递记录（可按状态/用户过滤，含 failed/sent）。"""
    agent = await _load_agent(db, agent_id)
    repo = SalesActionRepository(db)
    rows = await repo.list_deliveries_for_agent(
        agent.tenant_id, agent_id, status=status, user_id=user_id
    )
    return DeliveryListResponse(
        items=[DeliveryOut.model_validate(d) for d in rows], total=len(rows)
    )
