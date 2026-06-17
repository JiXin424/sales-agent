"""钉钉事件异步处理 Worker（HTTP 模式）。

使用 asyncio.Queue + asyncio.create_task 实现。
Worker 在 FastAPI lifespan 中启动/停止。

Stream 模式由 stream_client.py 独立处理，不使用此 worker。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sales_agent.core.config import get_settings
from sales_agent.integrations.dingtalk.config import DingTalkConfig

logger = logging.getLogger(__name__)

# 模块级状态
_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


def get_queue() -> asyncio.Queue:
    """获取或创建全局事件队列。"""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=100)
    return _queue


def enqueue_event(event_data: dict[str, Any]) -> None:
    """将事件入队（非阻塞）。"""
    queue = get_queue()
    try:
        queue.put_nowait(event_data)
        logger.debug("Enqueued DingTalk event: %s", event_data.get("event_id"))
    except asyncio.QueueFull:
        logger.warning("DingTalk event queue full, dropping event: %s", event_data.get("event_id"))


async def start_dingtalk_worker() -> None:
    """启动 HTTP 模式后台 Worker（在 FastAPI lifespan 中调用）。"""
    global _worker_task
    logger.info("Starting DingTalk HTTP background worker")
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_dingtalk_worker() -> None:
    """停止 HTTP 模式后台 Worker。"""
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    logger.info("DingTalk HTTP background worker stopped")


async def _worker_loop() -> None:
    """Worker 主循环：从队列取事件并处理。"""
    queue = get_queue()
    logger.info("DingTalk HTTP worker loop started")
    while True:
        try:
            event_data = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        try:
            await _process_event(event_data)
        except Exception as e:
            logger.error(
                "Error processing DingTalk event %s: %s",
                event_data.get("event_id"), e, exc_info=True,
            )


async def _process_event(event_data: dict[str, Any]) -> None:
    """处理单个钉钉事件（HTTP 模式）。"""
    from sales_agent.core.database import get_session_factory
    from sales_agent.core.tenant_runtime import get_tenant_runtime
    from sales_agent.integrations.dingtalk.message_sender import DingTalkMessageSender
    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event

    settings = get_settings()
    config = settings.dingtalk
    runtime = get_tenant_runtime()

    dingtalk_user_id = event_data.get("sender_id", "")

    # 定义回复函数（使用 Open API 发送）
    sender = DingTalkMessageSender(config)

    async def reply_fn(text: str) -> None:
        """通过 Open API 发送回复。"""
        if config.reply_format == "markdown":
            await sender.send_markdown(dingtalk_user_id, "销售助手", text)
        else:
            await sender.send_text(dingtalk_user_id, text)

    factory = get_session_factory()
    async with factory() as db:
        try:
            await handle_dingtalk_event(
                db, config, settings, runtime,
                event_id=event_data.get("event_id", ""),
                corp_id=event_data.get("corp_id", ""),
                sender_id=dingtalk_user_id,
                sender_name=event_data.get("sender_name", ""),
                message_type=event_data.get("message_type", "text"),
                text=event_data.get("text"),
                media_download_codes=event_data.get("media_download_codes") or [],
                raw_event=event_data.get("raw_event") or {},
                message_id=event_data.get("message_id", ""),
                dingtalk_conversation_id=event_data.get("conversation_id", ""),
                reply_fn=reply_fn,
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("Failed to process DingTalk event: %s", e, exc_info=True)
            # 尝试发送错误回复
            try:
                await sender.send_text(dingtalk_user_id, "我这边暂时无法处理，请稍后再试。")
            except Exception:
                logger.error("Failed to send error reply to DingTalk user")


async def process_event_sync(event) -> None:
    """同步处理事件（本地调试用）。"""
    event_data = {
        "event_id": event.event_id,
        "corp_id": event.corp_id,
        "sender_id": event.sender_id,
        "sender_name": event.sender_name,
        "message_type": event.message_type,
        "text": event.text,
        "media_download_codes": getattr(event, "media_download_codes", []),
        "raw_event": getattr(event, "raw_event", {}),
        "message_id": event.message_id,
        "conversation_id": event.conversation_id,
    }
    await _process_event(event_data)
