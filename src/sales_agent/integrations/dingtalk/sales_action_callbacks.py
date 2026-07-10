"""钉钉销售动作卡片按钮回调路由。

端点 ``POST /integrations/dingtalk/sales-actions/callback``：

1. 验签（复用 :class:`DingTalkSignatureVerifier` 既有范式）；
2. 解析 **内部回调契约** :class:`SalesActionCallbackRequest`；
3. 调用仓储状态机（complete / cancel / snooze，幂等）；
4. 通过 :meth:`DingTalkCardSender.update_card` 用回执 Markdown 重渲染原卡片。

.. note::
    钉钉互动卡片按钮回调的真实载荷形态（``cardRequestId`` / 按钮键值 /
    加密包装）与普通聊天事件不同，且本项目尚无线上样例。此处先定义干净的
    **内部契约** 并由本路由签名校验后归一化到该契约；真实钉钉载荷 → 内部
    契约的映射（:func:`normalize_dingtalk_card_callback`）留作生产接线 TODO，
    当前以直传为主，测试驱动的是内部 handler 契约。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.core.exceptions import DingTalkSignatureError
from sales_agent.integrations.dingtalk.card_sender import DingTalkCardSender
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.signature import DingTalkSignatureVerifier
from sales_agent.services.sales_actions.card_renderer import render_acknowledgement
from sales_agent.services.sales_actions.contracts import SalesActionScope
from sales_agent.services.sales_actions.repository import (
    ActionStateResult,
    SalesActionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/dingtalk/sales-actions", tags=["sales-actions"])


# ---------------------------------------------------------------------------
# 内部回调契约
# ---------------------------------------------------------------------------


class SalesActionCallbackRequest(BaseModel):
    """卡片按钮回调的内部契约（已从钉钉原始载荷归一化）。"""

    model_config = ConfigDict(extra="ignore")

    action_id: str = Field(..., description="目标动作卡片 ID")
    tenant_id: str
    agent_id: str
    user_id: str
    button: Literal["complete", "snooze", "cancel"]
    event_id: str = Field(..., description="按钮点击事件 ID（幂等去重用）")
    new_time: datetime | None = Field(
        None, description="snooze 时的新提醒时间（UTC 或带偏移）"
    )
    card_instance_id: str | None = Field(
        None, description="原卡片 outTrackId，用于 update_card 重渲染"
    )


class SalesActionCallbackResponse(BaseModel):
    status: str
    action_id: str
    reason_code: str


# ---------------------------------------------------------------------------
# 核心处理（可独立单测：注入 db session + fake sender）
# ---------------------------------------------------------------------------


async def handle_sales_action_callback(
    db: AsyncSession,
    req: SalesActionCallbackRequest,
    *,
    sender,
) -> ActionStateResult:
    """按按钮动作驱动状态机并回渲染卡片。

    - complete / cancel → 终态翻转 + 取消在途提醒；
    - snooze → 取消在途提醒 + 创建新 snooze 提醒（``req.new_time`` 必填）；
    - 完成后用 :func:`render_acknowledgement` 重渲染原卡片（若提供 card_instance_id）。
    """
    repo = SalesActionRepository(db)
    scope = SalesActionScope(
        tenant_id=req.tenant_id,
        agent_id=req.agent_id,
        user_id=req.user_id,
    )

    if req.button == "complete":
        result = await repo.complete_action(scope, req.action_id, event_id=req.event_id)
        ack_status = "done"
    elif req.button == "cancel":
        result = await repo.cancel_action(scope, req.action_id, event_id=req.event_id)
        ack_status = "cancelled"
    else:  # snooze
        if req.new_time is None:
            return ActionStateResult(
                action_id=req.action_id, status="not_found", reason_code="missing_new_time"
            )
        outcome = await repo.snooze_action(
            scope,
            req.action_id,
            event_id=req.event_id,
            new_time=req.new_time,
        )
        if isinstance(outcome, ActionStateResult):
            result = outcome
            # already_snoozed / not_found / already_terminal
            ack_status = "snoozed" if result.reason_code == "already_snoozed" else result.status
        else:
            result = ActionStateResult(
                action_id=req.action_id, status="snoozed", reason_code="snoozed"
            )
            ack_status = "snoozed"

    await db.flush()

    # 回渲染原卡片（非阻塞：失败仅记录，不影响状态机结果）。
    # 仅在确有状态翻转/幂等命中时才重渲染；动作不存在（not_found）不重渲染，
    # 避免把一张「已完成」回执写到一张与该动作无关的卡片上。
    if req.card_instance_id and result.status in {"done", "cancelled", "snoozed"}:
        try:
            md = render_acknowledgement(
                action_id=req.action_id, status=ack_status, new_time=req.new_time
            )
            await sender.update_card(req.card_instance_id, md)
        except Exception as exc:
            logger.warning(
                "sales action card re-render failed (action=%s): %s",
                req.action_id,
                exc,
            )

    return result


# ---------------------------------------------------------------------------
# 钉钉原始载荷 → 内部契约（生产接线 TODO）
# ---------------------------------------------------------------------------


def normalize_dingtalk_card_callback(raw: dict) -> SalesActionCallbackRequest:
    """把钉钉互动卡片按钮回调原始 JSON 归一化为内部契约。

    TODO(生产接线): 待拿到真实卡片回调样例后，在此映射：
      - 钉钉 ``cardRequestId`` / ``outTrackId`` → ``card_instance_id``；
      - 按钮键（如 ``btn_complete`` / ``btn_snooze``）→ ``button``；
      - 回调携带的 ``action_id`` / scope（需在卡片私有数据中预置）；
      - ``new_time`` 取自按钮 value 或 snooze 选择器。
    当前实现假定上游（或钉钉回调订阅）已按内部契约投递。
    """
    return SalesActionCallbackRequest.model_validate(raw)


# ---------------------------------------------------------------------------
# FastAPI 路由
# ---------------------------------------------------------------------------


def _get_dingtalk_config() -> DingTalkConfig:
    from sales_agent.core.config import get_settings

    return get_settings().dingtalk


def _verify_signature(config: DingTalkConfig, timestamp: str, sign: str, body: bytes) -> None:
    """与 routes.py 一致的签名校验；失败抛 401。"""
    verifier = DingTalkSignatureVerifier(config)
    if not verifier.verify(timestamp, sign):
        raise DingTalkSignatureError("Signature verification failed")


@router.post("/callback", response_model=SalesActionCallbackResponse)
async def sales_action_callback(
    request: Request,
    db: DbSession,
    timestamp: str = Header("", alias="timestamp"),
    sign: str = Header("", alias="sign"),
) -> SalesActionCallbackResponse:
    """接收并处理钉钉销售动作卡片按钮回调。"""
    config = _get_dingtalk_config()

    body = await request.body()

    # 签名校验（生产：钉钉回调带 timestamp/sign 头）
    try:
        _verify_signature(config, timestamp, sign, body)
    except DingTalkSignatureError as e:
        logger.warning("sales action callback signature failed: %s", e.detail)
        raise HTTPException(status_code=401, detail="Signature verification failed")

    try:
        raw = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")

    try:
        req = normalize_dingtalk_card_callback(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid callback payload: {e}")

    sender = DingTalkCardSender(config)
    try:
        result = await handle_sales_action_callback(db, req, sender=sender)
    finally:
        await sender.close()

    await db.commit()

    return SalesActionCallbackResponse(
        status=result.status, action_id=result.action_id, reason_code=result.reason_code
    )
