"""钉钉事件处理核心逻辑 — 被 Stream 和 HTTP 模式共用。

处理链路：
  用户映射 → 会话映射 → 命令解析 → Graph → 渲染 → 回复

Task 6 变更：
  - 返回 ``DingTalkTurnResult``（不再返回 ``None``）。
  - reset 命令不再本地生成新 ``conversation_id``，而是设置
    ``reset_requested=True`` 交给 Graph 清理 flow/topic 状态。
  - Graph/persistence 失败时发送尽力错误回复并 re-raise，让 worker
    rollback —— 绝不从失败事务返回成功结构。
  - 重复投递（``response_kind == "duplicate"``）有空的
    ``rendered_text`` 且不调用 ``reply_fn``。
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
from sales_agent.integrations.dingtalk.agent_resolver import resolve_dingtalk_agent_id
from sales_agent.integrations.dingtalk.turn_result import DingTalkTurnResult
from sales_agent.services.online_conversation import invoke_online_turn

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
) -> DingTalkTurnResult:
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

    Returns:
        ``DingTalkTurnResult`` describing the observable outcome.
    """
    start_time = time.time()
    tenant_id = runtime.tenant_id
    dingtalk_user_id = sender_id

    # --- 回退消息（错误提示等，直接发送不走 Agent） ---
    if message_type == "fallback":
        fallback_text = text or ""
        await reply_fn(fallback_text)
        return DingTalkTurnResult(
            event_id=event_id,
            tenant_id=tenant_id,
            agent_id=None,
            internal_user_id=None,
            dingtalk_user_id=dingtalk_user_id,
            conversation_id=None,
            thread_id=None,
            normalized_message=fallback_text,
            rendered_text=fallback_text,
            response_kind="fallback",
            latency_ms=int((time.time() - start_time) * 1000),
        )

    # --- 正常消息处理 ---
    from sales_agent.integrations.dingtalk.user_mapper import DingTalkUserMapper
    from sales_agent.integrations.dingtalk.conversation_mapper import DingTalkConversationMapper
    from sales_agent.integrations.dingtalk.command_parser import DingTalkCommandParser
    from sales_agent.integrations.dingtalk.media_adapter import DingTalkMediaAdapter

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
            media_text = "已收到你的图片或语音，但我暂时无法识别内容。请换成文字描述一下，我马上帮你处理。"
            await reply_fn(media_text)
            await adapter.close()
            return DingTalkTurnResult(
                event_id=event_id,
                tenant_id=tenant_id,
                agent_id=None,
                internal_user_id=None,
                dingtalk_user_id=dingtalk_user_id,
                conversation_id=None,
                thread_id=None,
                normalized_message=message_text,
                rendered_text=media_text,
                response_kind="media_error",
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            )
        finally:
            await adapter.close()

    # 1. 用户映射
    user_mapper = DingTalkUserMapper(db, tenant_id)
    internal_user_id = await user_mapper.get_or_create_user(
        corp_id=corp_id,
        dingtalk_user_id=dingtalk_user_id,
        display_name=sender_name,
    )

    # 2. 会话映射 — reset 不再本地生成新 ID，thread_id 跨 reset 保持稳定。
    conversation_id = DingTalkConversationMapper.generate_conversation_id(
        tenant_id, dingtalk_user_id,
    )

    # 3. 命令解析（DingTalk 专属的 reset 处理） — reset 通过 Graph 清理状态。
    command_parser = DingTalkCommandParser(settings.conversation.reset_commands)
    parsed = command_parser.parse(message_text)

    renderer = DingTalkMessageRenderer(config)

    reset_requested = False
    if parsed.is_reset:
        reset_requested = True
        # reset 带后缀 → 后缀作为新消息交给 Graph；无后缀 → 空消息触发确认回复。
        message_text = parsed.remaining_message

    # 4. 解析渠道绑定的 Agent ID → 调用 Online Graph（共享管道）
    dt_agent_id = await resolve_dingtalk_agent_id(db, tenant_id)

    try:
        result = await invoke_online_turn(
            db=db,
            tenant_id=tenant_id,
            agent_id=dt_agent_id,
            user_id=internal_user_id,
            session_user_id=sender_id,
            channel="dingtalk",
            conversation_id=conversation_id,
            message=message_text,
            event_id=event_id,
            reset_requested=reset_requested,
        )

        answer_dict = result.get("answer_dict") or result.get("final_answer") or {
            "summary": "", "sections": []
        }
        response_kind = result.get("response_kind", "chat")

        latency_ms = int((time.time() - start_time) * 1000)
        thread_id = result.get("thread_id")

        # 重复投递是 no-op：不渲染、不回复、不写会话日志、不推进流程。
        # Graph 的 duplicate_node 已返回 response_kind=="duplicate" 且无状态变更，
        # 这里直接提前返回，避免对用户造成二次打扰。
        if response_kind == "duplicate":
            logger.info("Skipping duplicate DingTalk event: %s", event_id)
            return DingTalkTurnResult(
                event_id=event_id,
                tenant_id=tenant_id,
                agent_id=dt_agent_id,
                internal_user_id=internal_user_id,
                dingtalk_user_id=dingtalk_user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                normalized_message=message_text,
                rendered_text="",
                answer_dict=answer_dict,
                response_kind="duplicate",
                topic_id=result.get("topic_id"),
                active_flow=result.get("active_flow"),
                flow_stage=result.get("flow_stage"),
                completed_flow=result.get("completed_flow"),
                flow_action=result.get("flow_action"),
                last_event_id=result.get("last_event_id"),
                memory_operation=result.get("memory_operation"),
                memory_status=result.get("memory_status"),
                memory_reason_code=result.get("memory_reason_code"),
                memory_ids=result.get("memory_ids"),
                memory_candidate_count=result.get("memory_candidate_count", 0),
                # User profile memory recall (Task 5)
                selected_memory_ids=result.get("selected_memory_ids"),
                profile_version=result.get("profile_version"),
                memory_trace=result.get("memory_trace"),
                memory_degraded=bool(result.get("memory_degraded")),
                memory_degradation_reason=result.get("memory_degradation_reason"),
                # Sales actions (Task 4)
                sales_action_operation=result.get("sales_action_operation"),
                sales_action_status=result.get("sales_action_status"),
                sales_action_id=result.get("sales_action_id"),
                sales_action_scheduled_at=result.get("sales_action_scheduled_at"),
                sales_action_reason_code=result.get("sales_action_reason_code"),
                latency_ms=latency_ms,
            )

        # 正常响应：渲染 + 回复
        sources = answer_dict.get("sources", [])
        risk_result = answer_dict.get("risk_result", {})
        rendered = renderer.render(answer_dict, sources, risk_result)
        await reply_fn(rendered)

        logger.info(
            "DingTalk event processed: event=%s user=%s task=%s path=%s latency=%dms",
            event_id, dingtalk_user_id,
            result.get("completed_flow") or result.get("active_flow") or answer_dict.get("task_type", "unknown"),
            result.get("flow_action") or response_kind,
            latency_ms,
        )

        return DingTalkTurnResult(
            event_id=event_id,
            tenant_id=tenant_id,
            agent_id=dt_agent_id,
            internal_user_id=internal_user_id,
            dingtalk_user_id=dingtalk_user_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            normalized_message=message_text,
            rendered_text=rendered,
            answer_dict=answer_dict,
            response_kind=response_kind,
            topic_id=result.get("topic_id"),
            previous_topic_id=result.get("previous_topic_id"),
            turn_relation=result.get("turn_relation"),
            standalone_query=result.get("standalone_query"),
            active_flow=result.get("active_flow"),
            flow_stage=result.get("flow_stage"),
            completed_flow=result.get("completed_flow"),
            flow_action=result.get("flow_action"),
            last_event_id=result.get("last_event_id"),
            memory_operation=result.get("memory_operation"),
            memory_status=result.get("memory_status"),
            memory_reason_code=result.get("memory_reason_code"),
            memory_ids=result.get("memory_ids"),
            memory_candidate_count=result.get("memory_candidate_count", 0),
            # User profile memory recall (Task 5)
            selected_memory_ids=result.get("selected_memory_ids"),
            profile_version=result.get("profile_version"),
            memory_trace=result.get("memory_trace"),
            memory_degraded=bool(result.get("memory_degraded")),
            memory_degradation_reason=result.get("memory_degradation_reason"),
            # Sales actions (Task 4)
            sales_action_operation=result.get("sales_action_operation"),
            sales_action_status=result.get("sales_action_status"),
            sales_action_id=result.get("sales_action_id"),
            sales_action_scheduled_at=result.get("sales_action_scheduled_at"),
            sales_action_reason_code=result.get("sales_action_reason_code"),
            latency_ms=latency_ms,
        )

    except Exception:
        # Graph/persistence 失败：发送尽力错误回复并 re-raise，让 worker rollback。
        # 绝不从失败事务返回成功结构。
        logger.error("Agent pipeline error for DingTalk event: %s", event_id, exc_info=True)
        try:
            await reply_fn(renderer.render_error("model_failed"))
        except Exception:
            logger.error("Failed to send error reply to DingTalk user")
        raise
