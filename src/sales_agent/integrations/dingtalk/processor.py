"""钉钉事件处理核心逻辑 — 被 Stream 和 HTTP 模式共用。

处理链路：
  用户映射 → 会话映射 → 命令解析 → ChatPipeline → 渲染 → 回复
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import Settings
from sales_agent.core.tenant_runtime import TenantRuntime
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.message_renderer import DingTalkMessageRenderer
from sales_agent.services.chat_pipeline import ChatPipeline

logger = logging.getLogger(__name__)

# 回复函数类型：接收文本，发送到钉钉
ReplyFn = Callable[[str], Awaitable[None]]


async def handle_dingtalk_event(
    db: AsyncSession,
    config: DingTalkConfig,
    settings: Settings,
    runtime: TenantRuntime,
    *,
    event_id: str,
    corp_id: str,
    sender_id: str,
    sender_name: str,
    message_type: str,
    text: str | None,
    media_download_codes: list[str] | None = None,
    raw_event: dict[str, Any] | None = None,
    message_id: str = "",
    dingtalk_conversation_id: str,
    reply_fn: ReplyFn,
) -> None:
    """处理钉钉单聊消息，调用 Agent 管道并回复。

    Args:
        db: 数据库 session
        config: 钉钉配置
        settings: 全局设置
        runtime: 租户运行时
        event_id: 事件 ID
        corp_id: 企业 ID
        sender_id: 钉钉用户 ID
        sender_name: 发送者昵称
        message_type: 消息类型 ("text", "fallback")
        text: 消息文本
        media_download_codes: 钉钉媒体 downloadCode 列表
        raw_event: 钉钉原始事件
        message_id: 钉钉消息 ID
        dingtalk_conversation_id: 钉钉会话 ID
        reply_fn: 回复函数 — 接收文本并发送到钉钉
    """
    start_time = time.time()

    # --- 回退消息（错误提示等，直接发送不走 Agent） ---
    if message_type == "fallback":
        await reply_fn(text or "")
        return

    # --- 正常消息处理 ---
    from sales_agent.models.base import generate_id
    from sales_agent.integrations.dingtalk.user_mapper import DingTalkUserMapper
    from sales_agent.integrations.dingtalk.conversation_mapper import DingTalkConversationMapper
    from sales_agent.integrations.dingtalk.command_parser import DingTalkCommandParser
    from sales_agent.integrations.dingtalk.media_adapter import DingTalkMediaAdapter

    dingtalk_user_id = sender_id
    message_text = text or ""

    if message_type != "text":
        adapter = DingTalkMediaAdapter(config, settings)
        try:
            message_text = await adapter.to_agent_text(
                message_type=message_type,
                text=text,
                download_codes=media_download_codes or [],
                raw_event=raw_event or {},
            )
        except Exception as e:
            logger.warning("DingTalk media adaptation failed: type=%s error=%s", message_type, e)
            await reply_fn("已收到你的图片或语音，但我暂时无法识别内容。请换成文字描述一下，我马上帮你处理。")
            return
        finally:
            await adapter.close()

    # 1. 用户映射
    user_mapper = DingTalkUserMapper(db, runtime.tenant_id)
    internal_user_id = await user_mapper.get_or_create_user(
        corp_id=corp_id,
        dingtalk_user_id=dingtalk_user_id,
        display_name=sender_name,
    )

    # 2. 会话映射
    conversation_id = DingTalkConversationMapper.generate_conversation_id(
        runtime.tenant_id, dingtalk_user_id,
    )

    # 3. 命令解析（DingTalk 专属的 reset 处理）
    command_parser = DingTalkCommandParser(settings.conversation.reset_commands)
    parsed = command_parser.parse(message_text)

    renderer = DingTalkMessageRenderer(config)

    # 4. 处理 reset 命令 — 生成新 conversation_id
    if parsed.is_reset:
        conversation_id = generate_id()
        if not parsed.remaining_message:
            await reply_fn("已开启新话题。你可以直接说当前要处理的销售问题。")
            return
        # 有剩余消息，继续处理
        message_text = parsed.remaining_message

    # 5. 调用 ChatPipeline（共享管道）
    # 从渠道绑定解析 agent_id（dingtalk 渠道绑定的 Agent）；
    # 解析失败时为 None → 管道回退到 tenant 默认 Agent（向后兼容）。
    dt_agent_id: str | None = None
    try:
        from sqlalchemy import select as _select
        from sales_agent.models.agent_channel_config import AgentChannelConfig
        from sales_agent.models.agent import Agent as _Agent
        row = (
            await db.execute(
                _select(AgentChannelConfig).where(
                    AgentChannelConfig.tenant_id == runtime.tenant_id,
                    AgentChannelConfig.channel == "dingtalk",
                    AgentChannelConfig.status.in_(("configured", "verified")),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            # 校验该 Agent 仍 active（非 paused/archived）
            a = (
                await db.execute(
                    _select(_Agent).where(_Agent.id == row.agent_id)
                )
            ).scalar_one_or_none()
            if a is not None and a.status == "active":
                dt_agent_id = a.id
    except Exception as e:
        logger.warning("DingTalk channel→agent resolution failed: %s", e)

    try:
        pipeline = ChatPipeline(db, settings)
        result = await pipeline.execute(
            tenant_id=runtime.tenant_id,
            user_id=internal_user_id,
            message=message_text,
            conversation_id=conversation_id,
            context=None,
            channel="dingtalk_single",
            reply_fn=reply_fn,
            agent_id=dt_agent_id,
        )

        # 快速命令直接回复
        if result.fast_reply:
            # reset 命令已经由 pipeline 返回新 conversation_id
            if result.path_result.reason == "reset_command":
                await reply_fn(result.fast_reply)
            else:
                await reply_fn(result.fast_reply)

            latency_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "DingTalk fast path: event=%s user=%s path=%s latency=%dms",
                event_id, dingtalk_user_id, result.path_result.path, latency_ms,
            )
            return

        # 正常响应：渲染 + 回复
        sources = result.sources
        risk_result = result.risk_result
        answer_dict = result.answer_dict

        rendered = renderer.render(answer_dict, sources, risk_result)
        await reply_fn(rendered)

        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "DingTalk event processed: event=%s user=%s task=%s path=%s latency=%dms",
            event_id, dingtalk_user_id,
            result.route_result.task_type if result.route_result else "unknown",
            result.path_result.path,
            latency_ms,
        )

    except Exception as e:
        logger.error("Agent pipeline error for DingTalk event: %s", e, exc_info=True)
        error_text = renderer.render_error("model_failed")
        try:
            await reply_fn(error_text)
        except Exception:
            logger.error("Failed to send error reply to DingTalk user")
