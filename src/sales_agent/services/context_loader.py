"""会话上下文加载服务。

实现三层上下文：短期消息窗口 + 会话摘要记忆 + 当前请求 context。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import get_settings
from sales_agent.llm.base import ChatModel
from sales_agent.models.conversation import ConversationMessage, ConversationSummary

logger = logging.getLogger(__name__)

# 连续指令关键词
_CONTINUATION_PATTERNS = [
    r"继续优化",
    r"基于刚才",
    r"上一版",
    r"上一条",
    r"再短一点",
    r"再长一点",
    r"换成.*语气",
    r"刚才那个",
    r"沿用上面",
    r"基于上一轮",
]


def is_continuation_intent(message: str) -> bool:
    """检测用户是否有继续上一轮对话的意图。"""
    for pattern in _CONTINUATION_PATTERNS:
        if re.search(pattern, message):
            return True
    return False


def is_reset_command(message: str, reset_commands: list[str] | None = None) -> bool:
    """检测用户是否要重置上下文。"""
    commands = reset_commands or get_settings().conversation.reset_commands
    stripped = message.strip()
    return stripped in commands


async def load_context(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    use_history: bool = True,
    reset_context: bool = False,
    current_message: str = "",
    chat_model: ChatModel | None = None,
) -> dict[str, Any]:
    """加载会话上下文。

    Returns:
        dict with keys:
        - conversation_id: str
        - history_messages: list[dict] (role, content)
        - summary_memory: str | None
        - active_context: dict
        - is_new_conversation: bool
    """
    settings = get_settings()

    # 检查是否是重置命令
    if is_reset_command(current_message):
        return {
            "conversation_id": conversation_id,
            "history_messages": [],
            "summary_memory": None,
            "active_context": {},
            "is_new_conversation": True,
        }

    # 如果明确要求重置或不用历史
    if reset_context or not use_history:
        return {
            "conversation_id": conversation_id,
            "history_messages": [],
            "summary_memory": None,
            "active_context": {},
            "is_new_conversation": False,
        }

    # 1. 加载短期消息窗口
    history_turns = settings.conversation.history_turns
    limit = history_turns * 2

    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = list(reversed(result.scalars().all()))

    history_messages = [{"role": m.role, "content": m.content} for m in messages]

    # 检查是否过期
    if messages:
        last_msg_time = messages[-1].created_at if messages else None
        if last_msg_time:
            expire_hours = settings.conversation.expire_after_hours
            try:
                last_dt = datetime.fromisoformat(last_msg_time)
                now = datetime.now(timezone.utc)
                if hasattr(last_dt, 'tzinfo') and last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                hours_elapsed = (now - last_dt).total_seconds() / 3600
                if hours_elapsed > expire_hours:
                    return {
                        "conversation_id": conversation_id,
                        "history_messages": [],
                        "summary_memory": None,
                        "active_context": {},
                        "is_new_conversation": True,
                    }
            except (ValueError, TypeError):
                pass

    # 2. 加载会话摘要记忆
    summary_memory = None
    active_context = {}
    summary_stmt = (
        select(ConversationSummary)
        .where(
            ConversationSummary.conversation_id == conversation_id,
            ConversationSummary.tenant_id == tenant_id,
        )
        .order_by(ConversationSummary.updated_at.desc())
        .limit(1)
    )
    summary_result = await db.execute(summary_stmt)
    summary_row = summary_result.scalar_one_or_none()

    if summary_row:
        summary_memory = summary_row.summary
        if summary_row.facts_json:
            try:
                active_context = json.loads(summary_row.facts_json)
            except json.JSONDecodeError:
                pass

    # 3. 连续指令检测：确保上一轮上下文可用
    if is_continuation_intent(current_message) and not history_messages:
        # 尝试从日志中查找上一轮
        logger.info(
            "Continuation intent detected but no history found for conv %s",
            conversation_id,
        )

    return {
        "conversation_id": conversation_id,
        "history_messages": history_messages,
        "summary_memory": summary_memory,
        "active_context": active_context,
        "is_new_conversation": len(messages) == 0,
    }


async def maybe_update_summary(
    db: AsyncSession,
    chat_model: ChatModel,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    force: bool = False,
) -> None:
    """检查是否需要更新会话摘要，如果需要则更新。

    触发条件（spec 14.3.6）：
    - 会话消息超过 summary_after_turns 轮
    - 或历史消息超过 summary_after_chars 字符
    """
    settings = get_settings()

    # 统计当前消息数
    count_stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.created_at.desc())
    )
    result = await db.execute(count_stmt)
    all_messages = list(reversed(result.scalars().all()))

    if len(all_messages) < settings.conversation.summary_after_turns * 2 and not force:
        total_chars = sum(len(m.content) for m in all_messages)
        if total_chars < settings.conversation.summary_after_chars:
            return

    if not all_messages:
        return

    # 生成摘要
    messages_text = "\n".join(
        f"{'用户' if m.role == 'user' else 'Agent'}: {m.content[:200]}"
        for m in all_messages
    )

    summary_prompt = f"""请根据以下对话内容生成结构化摘要：

{messages_text}

请以 JSON 格式输出：
{{
  "summary": "会话摘要",
  "facts": {{
    "stage": "销售阶段",
    "tone": "用户偏好的语气"
  }},
  "risk_notes": ["已触发的风险提醒"]
}}"""

    try:
        raw = await chat_model.generate(
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        # 解析 JSON
        json_match = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())

            # 查找已有摘要
            existing_stmt = (
                select(ConversationSummary)
                .where(
                    ConversationSummary.conversation_id == conversation_id,
                    ConversationSummary.tenant_id == tenant_id,
                )
                .order_by(ConversationSummary.updated_at.desc())
                .limit(1)
            )
            existing_result = await db.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()

            if existing:
                existing.summary = data.get("summary", "")
                existing.facts_json = json.dumps(data.get("facts", {}), ensure_ascii=False)
                existing.risk_notes_json = json.dumps(data.get("risk_notes", []), ensure_ascii=False)
            else:
                summary = ConversationSummary(
                    id=__import__("sales_agent.models.base", fromlist=["generate_id"]).generate_id(),
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    summary=data.get("summary", ""),
                    facts_json=json.dumps(data.get("facts", {}), ensure_ascii=False),
                    risk_notes_json=json.dumps(data.get("risk_notes", []), ensure_ascii=False),
                )
                db.add(summary)

            await db.flush()
            logger.info("Updated summary for conversation %s", conversation_id)

    except Exception as e:
        logger.error("Failed to update summary: %s", e, exc_info=True)
