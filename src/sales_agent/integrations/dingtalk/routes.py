"""钉钉集成 API 路由 — 核心消息收发。

端点：
  POST /integrations/dingtalk/events         — 接收钉钉事件回调
  GET  /integrations/dingtalk/health          — 钉钉健康检查
  POST /integrations/dingtalk/diagnostics/send-test — 测试发送消息

快捷入口（quick-entry）相关路由在 quick_entry.py 中独立维护，便于解耦。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import (
    ERROR_HTTP_STATUS,
    DingTalkRateLimitedError,
    DingTalkSignatureError,
    DingTalkTenantMismatchError,
    SalesAgentError,
)
from sales_agent.core.tenant_runtime import get_tenant_runtime
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.event_receiver import DingTalkEventReceiver
from sales_agent.integrations.dingtalk.schemas import (
    DingTalkEventAccepted,
    DingTalkHealthResponse,
    DingTalkSendTestRequest,
    DingTalkSendTestResponse,
)
from sales_agent.integrations.dingtalk.signature import DingTalkSignatureVerifier
from sales_agent.integrations.dingtalk.tenant_guard import DingTalkTenantGuard
from sales_agent.integrations.dingtalk.media_adapter import supported_media_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/dingtalk", tags=["dingtalk"])


def _get_dingtalk_config() -> DingTalkConfig:
    """获取钉钉配置。"""
    return get_settings().dingtalk


# ============================================================
# POST /integrations/dingtalk/events
# ============================================================


@router.post("/events", response_model=DingTalkEventAccepted)
async def receive_dingtalk_event(
    request: Request,
    db: DbSession,
    timestamp: str = Header("", alias="timestamp"),
    sign: str = Header("", alias="sign"),
) -> DingTalkEventAccepted:
    """接收钉钉单聊事件回调。

    流程：验签 → 解析 → 校验租户 → 去重 → 入库 → 入队 → 返回 200
    """
    config = _get_dingtalk_config()

    # 1. 检查钉钉是否启用
    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    # 2. 读取原始请求体
    body = await request.body()

    # 3. 验签 + 解析
    verifier = DingTalkSignatureVerifier(config)
    receiver = DingTalkEventReceiver(verifier)

    try:
        event = receiver.parse_and_verify(timestamp, sign, body)
    except DingTalkSignatureError as e:
        logger.warning("DingTalk signature failed: %s", e.detail)
        raise HTTPException(status_code=401, detail="Signature verification failed")
    except ValueError as e:
        logger.warning("DingTalk event parse error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    # 4. 校验租户
    guard = DingTalkTenantGuard(config)
    try:
        guard.verify_corp(event.corp_id)
    except DingTalkTenantMismatchError as e:
        logger.warning("DingTalk tenant mismatch: %s", e.detail)
        # 安全：不处理、不回复、只记录
        return DingTalkEventAccepted(status="rejected", event_id=event.event_id)

    # 5. 只处理单聊
    if event.conversation_type != "single":
        logger.debug("Ignoring non-single chat event: type=%s", event.conversation_type)
        return DingTalkEventAccepted(status="ignored", event_id=event.event_id)

    # 6. 去重
    from sales_agent.integrations.dingtalk.models import DingTalkInboundMessage

    is_dup = await _check_duplicate(db, config, event)
    if is_dup:
        logger.debug("Duplicate DingTalk event: %s", event.event_id)
        return DingTalkEventAccepted(status="accepted", event_id=event.event_id)

    # 7. 不支持的消息类型
    if event.message_type != "text" and not supported_media_type(event.message_type):
        # 入库记录（不处理）
        await _save_inbound(
            db, config, event, status="rejected",
            error_json=json.dumps({"reason": "unsupported_message_type", "type": event.message_type}),
        )
        # 异步发送友好提示
        await _enqueue_fallback_reply(config, event, "暂时只支持文字、图片和语音消息。")
        return DingTalkEventAccepted(status="accepted", event_id=event.event_id)

    # 8. 限流
    from sales_agent.integrations.dingtalk.rate_limiter import get_rate_limiter

    rate_limiter = get_rate_limiter(config)
    allowed, rate_error = rate_limiter.check(event.sender_id, config.corp_id)
    if not allowed:
        logger.warning("DingTalk rate limited: user=%s", event.sender_id)
        await _save_inbound(
            db, config, event, status="rejected",
            error_json=json.dumps({"reason": "rate_limited"}),
        )
        await _enqueue_fallback_reply(config, event, rate_error or "你发送得有点快，我先暂停处理一下。请稍后再试。")
        return DingTalkEventAccepted(status="accepted", event_id=event.event_id)

    # 9. 入库
    conversation_key = _make_conversation_key(config, event)
    await _save_inbound(db, config, event, status="pending", conversation_key=conversation_key)

    # 10. 入队异步处理
    if config.async_processing:
        from sales_agent.integrations.dingtalk.worker import enqueue_event
        enqueue_event({
            "event_id": event.event_id,
            "corp_id": event.corp_id,
            "sender_id": event.sender_id,
            "sender_name": event.sender_name,
            "message_type": event.message_type,
            "text": event.text,
            "media_download_codes": event.media_download_codes,
            "raw_event": event.raw_event,
            "message_id": event.message_id,
            "conversation_id": event.conversation_id,
        })
    else:
        # 同步降级（仅本地调试用）
        from sales_agent.integrations.dingtalk.worker import process_event_sync
        await process_event_sync(event)

    return DingTalkEventAccepted(status="accepted", event_id=event.event_id)


# ============================================================
# GET /integrations/dingtalk/health
# ============================================================


@router.get("/health", response_model=DingTalkHealthResponse)
async def dingtalk_health_check() -> DingTalkHealthResponse:
    """钉钉集成健康检查。"""
    config = _get_dingtalk_config()
    runtime = get_tenant_runtime()

    corp_id_bound = bool(config.corp_id)
    sender_ready = bool(config.app_key and config.app_secret and config.robot_code)

    return DingTalkHealthResponse(
        status="ok" if config.enabled else "disabled",
        tenant_id=runtime.tenant_id,
        message_mode=config.message_mode,
        corp_id_bound=corp_id_bound,
        sender_ready=sender_ready,
    )


# ============================================================
# POST /integrations/dingtalk/diagnostics/send-test
# ============================================================


@router.post("/diagnostics/send-test", response_model=DingTalkSendTestResponse)
async def send_test_message(
    req: DingTalkSendTestRequest,
    db: DbSession,
) -> DingTalkSendTestResponse:
    """发送测试消息到钉钉用户（管理员调试用）。"""
    config = _get_dingtalk_config()

    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    if not config.app_key or not config.app_secret or not config.robot_code:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")

    from sales_agent.integrations.dingtalk.message_sender import DingTalkMessageSender

    sender = DingTalkMessageSender(config)
    try:
        result = await sender.send_text(req.dingtalk_user_id, req.message)
        return DingTalkSendTestResponse(
            status="sent",
            message_id=result.get("message_id"),
        )
    except Exception as e:
        logger.error("DingTalk test send failed: %s", e, exc_info=True)
        return DingTalkSendTestResponse(
            status="failed",
            error=str(e),
        )


# ============================================================
# 内部辅助函数
# ============================================================


async def _check_duplicate(
    db: AsyncSession,
    config: DingTalkConfig,
    event,
) -> bool:
    """检查事件是否已处理（去重）。"""
    from sales_agent.integrations.dingtalk.models import DingTalkInboundMessage

    # 优先按 message_id 去重
    if event.message_id:
        stmt = select(DingTalkInboundMessage).where(
            DingTalkInboundMessage.dingtalk_message_id == event.message_id,
            DingTalkInboundMessage.tenant_id == config.corp_id,
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return True

    # 降级：按哈希去重
    dedup_hash = _make_dedup_hash(event)
    stmt = select(DingTalkInboundMessage).where(
        DingTalkInboundMessage.conversation_key == dedup_hash,
        DingTalkInboundMessage.tenant_id == config.corp_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _save_inbound(
    db: AsyncSession,
    config: DingTalkConfig,
    event,
    status: str,
    conversation_key: str = "",
    error_json: str | None = None,
) -> None:
    """保存入站消息到数据库。"""
    from sales_agent.integrations.dingtalk.models import DingTalkInboundMessage
    from sales_agent.models.base import generate_id

    runtime = get_tenant_runtime()
    msg = DingTalkInboundMessage(
        id=generate_id(),
        tenant_id=runtime.tenant_id,
        corp_id=event.corp_id,
        dingtalk_user_id=event.sender_id,
        dingtalk_message_id=event.message_id or None,
        conversation_key=conversation_key or _make_dedup_hash(event),
        message_type=event.message_type,
        text=event.text,
        raw_event_json=json.dumps(event.raw_event, ensure_ascii=False) if event.raw_event else None,
        status=status,
        error_json=error_json,
    )
    db.add(msg)
    await db.flush()


def _make_dedup_hash(event) -> str:
    """生成去重哈希键。"""
    raw = f"{event.corp_id}:{event.sender_id}:{event.text or ''}:{event.message_id or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _make_conversation_key(config: DingTalkConfig, event) -> str:
    """生成会话键。"""
    return f"dingtalk_single:{config.corp_id}:{event.sender_id}"


async def _enqueue_fallback_reply(config: DingTalkConfig, event, text: str) -> None:
    """将降级/错误回复加入异步队列。"""
    if config.async_processing:
        from sales_agent.integrations.dingtalk.worker import enqueue_event
        enqueue_event({
            "event_id": event.event_id,
            "corp_id": event.corp_id,
            "sender_id": event.sender_id,
            "sender_name": event.sender_name,
            "message_type": "fallback",
            "text": text,
            "message_id": event.message_id,
            "conversation_id": event.conversation_id,
        })
