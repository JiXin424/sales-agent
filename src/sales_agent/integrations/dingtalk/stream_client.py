"""钉钉 Stream 模式客户端。

使用 dingtalk-stream SDK 建立常驻 WebSocket 连接，
收到消息后根据配置选择流式卡片或传统回复模式。

Stream 模式优势：
- 不需要公网回调 URL
- SDK 内置断线重连和 ping keepalive
- 部署更简单（NAT/防火墙友好）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import dingtalk_stream

# --- websockets ping 兼容补丁 ---
# websockets 15.x 默认 ping_interval=20s + ping_timeout=20s，与钉钉 wss 网关
# 不兼容：网关对高频 ping 响应慢，20s 内无 pong 即抛 ConnectionClosedError，
# 导致 dingtalk_stream SDK [start] network exception 反复重连，连接抖动期间
# 钉钉推的消息送不进来（文字/图片/知识库查询全失效）。SDK 调
# websockets.connect(uri) 未覆盖 ping 参数，且自带 60s keepalive，故禁用
# websockets 自带 ping，只用 SDK keepalive。
# 仅当调用方未显式传 ping_interval/ping_timeout 时生效，不影响其他调用方。
import websockets

if getattr(websockets.connect.__init__, "_sales_agent_ping_patched", False) is False:
    _orig_connect_init = websockets.connect.__init__

    def _patched_connect_init(self, *args, **kwargs):
        if "ping_interval" not in kwargs:
            kwargs["ping_interval"] = None
        if "ping_timeout" not in kwargs:
            kwargs["ping_timeout"] = None
        return _orig_connect_init(self, *args, **kwargs)

    _patched_connect_init._sales_agent_ping_patched = True  # type: ignore[attr-defined]
    websockets.connect.__init__ = _patched_connect_init

from sales_agent.core.config import get_settings
from sales_agent.core.database import get_session_factory
from sales_agent.core.tenant_runtime import get_tenant_runtime
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event
from sales_agent.integrations.dingtalk.media_adapter import (
    DingTalkMediaAdapter,
    extract_download_codes,
    supported_media_type,
)

logger = logging.getLogger(__name__)

# 快速命令集合（不走流式卡片，直接回复）
_FAST_COMMANDS = {"帮助", "help", "？", "?", "新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}


def _should_stream(config: DingTalkConfig, message_type: str, text_content: str) -> bool:
    """Decide whether a DingTalk message takes the streaming card path.

    Text streams (unless it is a fast command). Supported media streams only
    when ``media_enabled`` is on — so voice/image get the same streaming card
    treatment as text. Everything else stays on the non-streaming path.
    """
    if not config.streaming_enabled:
        return False
    if message_type == "text":
        return text_content.strip() not in _FAST_COMMANDS
    if supported_media_type(message_type):
        return config.media_enabled
    return False


async def _resolve_streaming_message(
    config: DingTalkConfig,
    settings,
    *,
    card_sender,
    message_type: str,
    text_content: str,
    media_download_codes: list[str],
    raw_event: dict,
    dingtalk_user_id: str,
    reply_fn: Callable[[str], Awaitable[None]] | None,
) -> tuple[str | None, str | None]:
    """Prepare the message text for the streaming path.

    Text passes straight through and opens no card (returns ``(text, None)``).
    Media opens a "正在识别…" recognition card immediately (so the user gets
    feedback during the blocking ASR/vision step), runs media adaptation to
    text, and returns ``(text, card_id)`` so the streaming graph reuses that
    same card. On adaptation failure the card is finalized with a friendly
    error and ``(None, card_id)`` is returned to signal the caller to stop.
    """
    if message_type == "text":
        return text_content, None

    card_id = await card_sender.send_markdown_card(
        dingtalk_user_id=dingtalk_user_id,
        title="正在识别...",
        markdown_text="正在识别你的语音/图片…",
    )
    adapter = DingTalkMediaAdapter(config, settings)
    try:
        message_text = await adapter.to_agent_text(
            message_type=message_type,
            text=text_content,
            download_codes=media_download_codes or [],
            raw_event=raw_event or {},
        )
        return message_text, card_id
    except Exception as e:
        logger.warning(
            "DingTalk streaming media adaptation failed: type=%s error=%s",
            message_type, e,
        )
        error_text = "已收到你的图片或语音，但我暂时无法识别内容。请换成文字描述一下，我马上帮你处理。"
        try:
            await card_sender.streaming_finalize(card_id, error_text)
        except Exception:
            logger.error("Failed to finalize recognition card after media error")
            if reply_fn is not None:
                await reply_fn(error_text)
        return None, card_id
    finally:
        await adapter.close()


class SalesAgentChatbotHandler(dingtalk_stream.ChatbotHandler):
    """钉钉 Stream 模式消息处理器。

    收到 Stream 消息后：
    1. 过滤：只处理单聊文本消息
    2. 转换：构造事件参数
    3. 处理：根据配置选择流式卡片或传统回复
    4. 回复：通过 session webhook 或互动卡片 API
    """

    def __init__(self, config: DingTalkConfig):
        super().__init__()
        self._config = config
        self._card_sender = None

    def _get_card_sender(self):
        """懒初始化 CardSender。"""
        global _card_sender
        if self._card_sender is None and self._config.streaming_enabled:
            from sales_agent.integrations.dingtalk.card_sender import DingTalkCardSender
            self._card_sender = DingTalkCardSender(self._config)
            _card_sender = self._card_sender
        return self._card_sender

    async def process(
        self, callback: dingtalk_stream.CallbackMessage,
    ) -> tuple[int, str]:
        """处理钉钉 Stream 回调消息。

        SDK 会在收到 WebSocket 帧后调用此方法。
        返回 (status_code, message) 作为 ACK。
        """
        try:
            incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        except Exception as e:
            logger.error("Failed to parse DingTalk Stream message: %s", e)
            return dingtalk_stream.AckMessage.STATUS_OK, "parse_error"

        # 1. 只处理单聊
        if getattr(incoming, "conversation_type", "2") != "1":
            logger.debug("Ignoring non-single chat Stream message")
            return dingtalk_stream.AckMessage.STATUS_OK, "ignored"

        # 2. 提取文本和媒体 downloadCode
        message_type = getattr(incoming, "message_type", "") or "text"
        text_parts = []
        try:
            text_parts = incoming.get_text_list() or []
        except Exception:
            text_parts = []
        text_content = "\n".join(str(t).strip() for t in text_parts if str(t).strip())
        if not text_content and hasattr(incoming, "text") and incoming.text and hasattr(incoming.text, "content"):
            text_content = (incoming.text.content or "").strip()

        media_download_codes = []
        try:
            media_download_codes = incoming.get_image_list() or []
        except Exception:
            media_download_codes = []
        raw_event = callback.data if isinstance(callback.data, dict) else {}
        if not media_download_codes:
            media_download_codes = extract_download_codes(raw_event)

        if not text_content and message_type == "text":
            self.reply_text("暂时只支持文字、图片和语音消息。", incoming)
            return dingtalk_stream.AckMessage.STATUS_OK, "unsupported"
        if message_type != "text" and not supported_media_type(message_type):
            self.reply_text("暂时只支持文字、图片和语音消息。", incoming)
            return dingtalk_stream.AckMessage.STATUS_OK, "unsupported"

        # 3. 提取发送者信息
        corp_id = getattr(incoming, "chatbot_corp_id", "") or ""
        sender_id = getattr(incoming, "sender_staff_id", "") or getattr(incoming, "sender_id", "") or ""
        sender_name = getattr(incoming, "sender_nick", "") or ""
        message_id = getattr(incoming, "message_id", "") or ""
        conversation_id = getattr(incoming, "conversation_id", "") or ""

        # 4. 获取运行时上下文
        settings = get_settings()
        runtime = get_tenant_runtime()

        # 5. 定义回复函数（使用 session webhook，用于 fallback 和快速命令）
        async def reply_fn(text: str) -> None:
            """通过 session webhook 回复。"""
            try:
                if self._config.reply_format == "markdown":
                    self.reply_markdown("销售助手", text, incoming)
                else:
                    self.reply_text(text, incoming)
            except Exception as e:
                logger.error("Failed to reply via session webhook: %s", e)
                # 降级：尝试纯文本
                try:
                    self.reply_text(text, incoming)
                except Exception:
                    logger.error("Failed to reply via text fallback: %s", e)

        # 6. 判断是否走流式卡片（文字 + 受支持的语音/图片）
        use_streaming = _should_stream(self._config, message_type, text_content)

        factory = get_session_factory()
        async with factory() as db:
            try:
                if use_streaming:
                    await self._handle_streaming(
                        db=db,
                        settings=settings,
                        runtime=runtime,
                        event_id=f"ding_{message_id}" if message_id else f"ding_stream_{id(callback)}",
                        corp_id=corp_id,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        message_type=message_type,
                        text_content=text_content,
                        media_download_codes=media_download_codes,
                        raw_event=raw_event,
                        message_id=message_id,
                        dingtalk_conversation_id=conversation_id,
                        reply_fn=reply_fn,
                    )
                else:
                    # 原有非流式路径
                    await handle_dingtalk_event(
                        db, self._config, settings, runtime,
                        event_id=f"ding_{message_id}" if message_id else f"ding_stream_{id(callback)}",
                        corp_id=corp_id,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        message_type=message_type,
                        text=text_content,
                        media_download_codes=media_download_codes,
                        raw_event=raw_event,
                        message_id=message_id,
                        dingtalk_conversation_id=conversation_id,
                        reply_fn=reply_fn,
                    )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("Stream event processing failed: %s", e, exc_info=True)
                # 降级到非流式回复
                try:
                    await reply_fn("我这边暂时无法处理，请稍后再试。")
                except Exception:
                    pass

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    async def _handle_streaming(
        self,
        db,
        settings,
        runtime,
        *,
        event_id,
        corp_id,
        sender_id,
        sender_name,
        message_type,
        text_content,
        media_download_codes,
        raw_event,
        message_id,
        dingtalk_conversation_id,
        reply_fn,
    ):
        """流式处理路径：卡片 + LLM streaming。"""
        from sales_agent.integrations.dingtalk.graph_stream import (
            handle_dingtalk_stream_via_graph,
        )
        from sales_agent.integrations.dingtalk.user_mapper import DingTalkUserMapper
        from sales_agent.integrations.dingtalk.conversation_mapper import DingTalkConversationMapper
        from sales_agent.models.base import generate_id

        # 用户映射
        user_mapper = DingTalkUserMapper(db, runtime.tenant_id)
        internal_user_id = await user_mapper.get_or_create_user(
            corp_id=corp_id,
            dingtalk_user_id=sender_id,
            display_name=sender_name,
        )

        # 会话映射
        conversation_id = DingTalkConversationMapper.generate_conversation_id(
            runtime.tenant_id, sender_id,
        )

        card_sender = self._get_card_sender()
        if card_sender is None:
            # CardSender 初始化失败，降级到非流式（透传真实消息类型与媒体字段，
            # 由 handle_dingtalk_event 的媒体分支兜底，避免把媒体硬当 text 处理）
            logger.warning("CardSender not available, falling back to non-streaming")
            await handle_dingtalk_event(
                db, self._config, settings, runtime,
                event_id=event_id,
                corp_id=corp_id,
                sender_id=sender_id,
                sender_name=sender_name,
                message_type=message_type,
                text=text_content,
                media_download_codes=media_download_codes,
                raw_event=raw_event,
                message_id=message_id,
                dingtalk_conversation_id=dingtalk_conversation_id,
                reply_fn=reply_fn,
            )
            return

        # 媒体消息：先开「正在识别…」过渡卡片并转写成文字；文字消息直接透传、不开卡。
        message_text, preliminary_card_id = await _resolve_streaming_message(
            self._config,
            settings,
            card_sender=card_sender,
            message_type=message_type,
            text_content=text_content,
            media_download_codes=media_download_codes,
            raw_event=raw_event,
            dingtalk_user_id=sender_id,
            reply_fn=reply_fn,
        )
        if message_text is None:
            # 媒体识别失败：兜底已通过卡片/回复发出，不再进入 Graph。
            return

        # Resolve model provider for graph execution
        from sales_agent.integrations.dingtalk.agent_resolver import resolve_dingtalk_agent_id
        from sales_agent.services.tenant_resolver import TenantResolver

        agent_id = await resolve_dingtalk_agent_id(db, runtime.tenant_id)
        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(runtime.tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        result = await handle_dingtalk_stream_via_graph(
            tenant_id=runtime.tenant_id,
            user_id=internal_user_id,
            dingtalk_user_id=sender_id,
            message=message_text,
            conversation_id=conversation_id,
            agent_id=agent_id,
            event_id=event_id,
            reply_fn=reply_fn,
            card_sender=card_sender,
            db=db,
            chat_model=model_provider.chat,
            embedding_model=model_provider.embedding,
            card_id=preliminary_card_id,
        )
        logger.info("Graph-based streaming completed successfully")
        return


# ============================================================
# Stream Worker 管理
# ============================================================

_stream_task: asyncio.Task | None = None
_card_sender: "DingTalkCardSender | None" = None


async def start_dingtalk_stream_worker() -> None:
    """启动 Stream 模式 worker（在 FastAPI lifespan 中调用）。"""
    global _stream_task

    settings = get_settings()
    config = settings.dingtalk
    runtime = get_tenant_runtime()

    credential = dingtalk_stream.Credential(config.app_key, config.app_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)

    handler = SalesAgentChatbotHandler(config)
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC,
        handler,
    )

    # 卡片按钮回调（点赞/点踩/问题反馈多选等）——无条件注册，采集落库。
    # 不发卡片的租户永不收到该 topic，零副作用；handler 内部防御式恒 ACK。
    from sales_agent.integrations.dingtalk.card_callback_handler import (
        SalesAgentCardCallbackHandler,
    )

    client.register_callback_handler(
        dingtalk_stream.CallbackHandler.TOPIC_CARD_CALLBACK,
        SalesAgentCardCallbackHandler(runtime.tenant_id),
    )

    _stream_task = asyncio.create_task(client.start())
    logger.info(
        "DingTalk Stream worker started: tenant=%s, corp_id=%s, streaming=%s",
        runtime.tenant_id,
        config.corp_id,
        config.streaming_enabled,
    )


async def stop_dingtalk_stream_worker() -> None:
    """停止 Stream 模式 worker。"""
    global _stream_task, _card_sender
    if _stream_task:
        _stream_task.cancel()
        try:
            await _stream_task
        except (asyncio.CancelledError, Exception):
            pass
        _stream_task = None
    if _card_sender:
        try:
            await _card_sender.close()
        except Exception:
            pass
        _card_sender = None
    logger.info("DingTalk Stream worker stopped")
