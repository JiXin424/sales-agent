"""快捷入口多轮教练对话 — 小赢欣赏 / 卡点破框。

逻辑移植自 Yanshi_Omni_Agent 的 ``api/core/coach/small_win.py`` 与
``sales_block_breakthrough.py``（已验证的文案与状态机），改为**落库**存会话状态：
每次点击按钮 ``start_session`` 建一条 ``quick_sessions`` 记录并发首轮提问；
用户在钉钉单聊里的后续回复由 ``streaming_handler`` 顶部调用
``advance_active_session`` 推进，直到 ``completed`` 出卡。

对外只暴露三个入口：
  - ``VALID_TYPES`` / ``label_of``
  - ``start_session(db, tenant_id, external_user_id, session_type, ...) -> str``（首轮提问）
  - ``advance_active_session(db, tenant_id, external_user_id, user_text) -> (reply, completed) | None``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.quick_session import QuickSession

logger = logging.getLogger(__name__)

VALID_TYPES = ("small_win_appreciation", "sales_block_breakthrough")

_LABELS = {
    "small_win_appreciation": "小赢欣赏",
    "sales_block_breakthrough": "卡点破框",
}


def label_of(session_type: str) -> str:
    return _LABELS.get(session_type, session_type)


# 出卡后“软关闭”窗口：会话 completed 后 N 秒内，该用户在单聊里的回复仍由
# 本会话拦截（回一句固定确认语、不走 LLM），避免出卡文案末尾“承诺和回传”
# 诱导的回复掉进普通销售对话管道。超窗后才放行普通对话。
FOLLOWUP_WINDOW_SECONDS = 600

_FOLLOWUP_REPLIES = {
    "small_win_appreciation": (
        "✅ 这条小赢欣赏已结束。\n"
        "小赢已记下，继续保持，下一个进展随时再来。"
    ),
    "sales_block_breakthrough": (
        "✅ 这条卡点破框已结束。\n"
        "你按选定的最小行动做完，把客户反应发回来；"
        "要拆下一个卡点，就再点「卡点破框」重新开始。"
    ),
}
_FOLLOWUP_DEFAULT = "✅ 这条快捷对话已结束，需要时再点入口重新开始。"


from sales_agent.graph.guided_flow.handlers.coach_flows import (
    _ADVANCERS,
    _STARTERS,
    _is_cancel,
)


# ============================================================
# DB 会话服务
# ============================================================


async def _get_active(
    db: AsyncSession, tenant_id: str, external_user_id: str, channel: str = "dingtalk",
) -> QuickSession | None:
    stmt = (
        select(QuickSession)
        .where(
            QuickSession.tenant_id == tenant_id,
            QuickSession.external_user_id == external_user_id,
            QuickSession.channel == channel,
            QuickSession.status == "active",
        )
        .order_by(QuickSession.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_recent_completed(
    db: AsyncSession,
    tenant_id: str,
    external_user_id: str,
    channel: str,
    within_seconds: int,
) -> QuickSession | None:
    """查该用户最近 within_seconds 内「自然走完出卡」的快捷会话。

    仅命中 stage=completed 的会话（_sw/_sb_advance 出卡时置 stage=completed）；
    主动退出（_is_cancel）或被作废的旧会话 stage 仍为中间值，故不拦截——那些
    场景用户应能正常提问。updated_at 在 completed 时由 onupdate 刷新为出卡时刻。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).isoformat()
    stmt = (
        select(QuickSession)
        .where(
            QuickSession.tenant_id == tenant_id,
            QuickSession.external_user_id == external_user_id,
            QuickSession.channel == channel,
            QuickSession.status == "completed",
            QuickSession.stage == "completed",
            QuickSession.updated_at >= cutoff,
        )
        .order_by(QuickSession.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def has_active_or_recent_session(
    db: AsyncSession, tenant_id: str, external_user_id: str, channel: str = "dingtalk",
) -> bool:
    """是否存在活跃会话、或出卡后软关闭窗口内的会话。供上层决定是否解析模型。"""
    if await _get_active(db, tenant_id, external_user_id, channel):
        return True
    return await _get_recent_completed(
        db, tenant_id, external_user_id, channel, FOLLOWUP_WINDOW_SECONDS,
    ) is not None


async def start_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    external_user_id: str,
    session_type: str,
    agent_id: str | None = None,
    channel: str = "dingtalk",
) -> str:
    """开始一次快捷入口会话：作废旧活跃会话，建新会话，返回首轮提问文案。"""
    if session_type not in VALID_TYPES:
        raise ValueError(f"invalid session_type: {session_type}")

    # 作废该用户当前所有活跃会话（同一时间只进行一个）
    existing = await _get_active(db, tenant_id, external_user_id, channel)
    if existing is not None:
        existing.status = "completed"

    stage, payload, first_reply = _STARTERS[session_type]()
    session = QuickSession(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel=channel,
        external_user_id=external_user_id,
        session_type=session_type,
        stage=stage,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="active",
    )
    db.add(session)
    await db.flush()
    logger.info(
        "quick-session started: type=%s user=%s stage=%s",
        session_type, external_user_id, stage,
    )
    return first_reply


async def advance_active_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    external_user_id: str,
    user_text: str,
    channel: str = "dingtalk",
    chat_model: Any = None,
) -> tuple[str, bool] | None:
    """若有活跃会话则推进一轮，返回 (reply, completed)。

    无活跃会话时：若该用户最近 ``FOLLOWUP_WINDOW_SECONDS`` 秒内有过自然出卡的快捷
    会话（出卡后软关闭窗口），仍拦截其回复并返回确认语 ``(reply, True)``，避免出卡
    文案诱导的承诺/回传回复掉进普通 LLM 对话；否则返回 None，交由上层走普通管道。
    """
    session = await _get_active(db, tenant_id, external_user_id, channel)
    if session is None:
        recent = await _get_recent_completed(
            db, tenant_id, external_user_id, channel, FOLLOWUP_WINDOW_SECONDS,
        )
        if recent is not None:
            logger.info(
                "quick-session followup ack: type=%s user=%s completed_at=%s",
                recent.session_type, external_user_id, recent.updated_at,
            )
            return _FOLLOWUP_REPLIES.get(recent.session_type, _FOLLOWUP_DEFAULT), True
        logger.info(
            "quick-session miss: tenant=%s user=%s channel=%s -> fallback to pipeline",
            tenant_id, external_user_id, channel,
        )
        return None

    # 允许用户随时退出多轮会话，避免被「锁」在流程里无法正常提问
    if _is_cancel(user_text):
        session.status = "completed"
        await db.flush()
        logger.info(
            "quick-session cancelled by user: type=%s user=%s",
            session.session_type, external_user_id,
        )
        return f"已退出「{label_of(session.session_type)}」，你可以正常提问了。", True

    advancer = _ADVANCERS.get(session.session_type)
    if advancer is None:
        return None

    try:
        payload = json.loads(session.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}

    # 解析 coach prompt（接入 DB 版本管理；失败回退内置）
    from sales_agent.services.prompt_resolver_helper import resolve_quick_session_prompts
    prompts = await resolve_quick_session_prompts(db, tenant_id, session.agent_id)

    new_stage, new_payload, reply, completed = await advancer(
        chat_model, session.stage, payload, user_text, prompts,
    )
    session.stage = new_stage
    session.payload_json = json.dumps(new_payload, ensure_ascii=False)
    if completed:
        session.status = "completed"
    await db.flush()
    logger.info(
        "quick-session advanced: type=%s user=%s stage=%s completed=%s",
        session.session_type, external_user_id, new_stage, completed,
    )
    return reply, completed
