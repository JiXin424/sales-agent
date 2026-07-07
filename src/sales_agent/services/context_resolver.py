"""Context Resolver — 话语和话题关系解析。

输入用户消息、当前话题（可选）和最近消息，调用 LLM 输出
:class:`ContextDecision`，包含话题关系分类和重写后的独立查询。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.prompts.context_resolver_prompt import CONTEXT_RESOLVER_PROMPT
from sales_agent.services.prompt_resolver_helper import resolve_router_prompt
from sales_agent.services.structured_router_output import ContextDecision, parse_model_json

logger = logging.getLogger(__name__)

# 显式延续标记 — 用户明确表示继续说当前话题
_CONTINUATION_MARKERS = {"继续", "接着", "然后", "还有", "另外", "再", "接着说", "继续讲"}

# 显式新话题标记 — 用户明确表示切换/开始新话题
_NEW_TOPIC_MARKERS = {"新话题", "换一个", "reset", "换话题", "不谈这个", "问个别的"}


def _deterministic_fallback(
    message: str,
    topic: ConversationTopic | None,
) -> ContextDecision:
    """LLM 两次输出都无法解析时的确定性兜底策略。

    优先级：
    1. 有当前话题 + 显式延续标记 → continue
    2. 显式新话题标记 → new
    3. 其余情况 → ambiguous（reason_code="resolver_failure"）
    """
    # 显式延续标记：仅当有当前话题时才有意义
    if topic is not None and any(m in message for m in _CONTINUATION_MARKERS):
        return ContextDecision(
            turn_relation="continue",
            standalone_query=message,
            retained_entities=[],
            retracted_goals=[],
            missing_references=[],
            confidence=0.5,
            reason_code="resolver_failure",
        )

    # 显式新话题标记
    if any(m in message for m in _NEW_TOPIC_MARKERS):
        return ContextDecision(
            turn_relation="new",
            standalone_query=message,
            retained_entities=[],
            retracted_goals=[],
            missing_references=[],
            confidence=0.5,
            reason_code="resolver_failure",
        )

    # 兜底：无法确定
    return ContextDecision(
        turn_relation="ambiguous",
        standalone_query="",
        retained_entities=[],
        retracted_goals=[],
        missing_references=[],
        confidence=0.5,
        reason_code="resolver_failure",
    )


def _build_messages(
    message: str,
    topic: ConversationTopic | None,
    recent_messages: list[dict[str, str]],
    system_prompt: str,
) -> list[dict[str, str]]:
    """构造 LLM 调用消息列表。"""
    user_content = f"当前消息：{message}\n"

    if topic is not None:
        user_content += f"\n当前话题摘要：{topic.summary}\n"
        user_content += f"当前目标：{topic.current_goal}\n"

    if recent_messages:
        user_content += "\n最近消息（从旧到新）：\n"
        for msg in recent_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            user_content += f"  {role}：{content}\n"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


async def resolve_context(
    *,
    message: str,
    topic: ConversationTopic | None,
    recent_messages: list[dict[str, str]],
    chat_model: Any,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> ContextDecision:
    """解析用户消息与对话上下文的话题关系，生成 :class:`ContextDecision`。

    内部重试逻辑：首次解析失败时追加 schema-error 提示重试；
    两次均失败则使用 :func:`_deterministic_fallback` 兜底。

    Parameters
    ----------
    message :
        用户最新输入消息。
    topic :
        当前活跃的 ConversationTopic（可为 None）。
    recent_messages :
        最近消息列表，每项含 ``role`` 和 ``content`` 键。
    chat_model :
        支持 ``generate(messages, temperature, max_tokens)`` 的模型实例。
    db :
        数据库会话；非空且 *tenant_id* 有值时走 PromptRegistry 三级回退
        （运营后台编辑生效），否则回退到模块常量 :data:`CONTEXT_RESOLVER_PROMPT`。
    tenant_id, agent_id :
        租户 / Agent 标识，用于 PromptRegistry 解析。

    Returns
    -------
    ContextDecision
        解析后的结构化决策。
    """
    system_prompt = await resolve_router_prompt(
        db,
        "context_resolver",
        tenant_id,
        agent_id,
        default=CONTEXT_RESOLVER_PROMPT,
    )
    messages = _build_messages(message, topic, recent_messages, system_prompt)

    for attempt in range(2):
        try:
            response = await chat_model.generate(
                messages=messages,
                temperature=0.0,
                max_tokens=500,
            )
            return parse_model_json(response, ContextDecision)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "resolve_context parse failure (attempt %d/2): %s",
                attempt + 1,
                exc,
            )
            if attempt == 0:
                # 追加 schema-error 提示让模型重新输出
                messages.append({
                    "role": "user",
                    "content": "您的输出格式不符合 JSON 规范，请仅输出一个合法的 JSON 对象，不要包含其他任何内容。",
                })
                continue

            # 第二次失败，使用确定性兜底
            return _deterministic_fallback(message, topic)

    # 不应到达此处，但为类型安全保留
    return _deterministic_fallback(message, topic)
