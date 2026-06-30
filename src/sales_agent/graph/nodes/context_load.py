"""Context loading node.

Loads recent conversation history from the database via Runtime.context.
"""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime
from sqlalchemy import select

from sales_agent.graph.state import ChatGraphState
from sales_agent.models.conversation import ConversationMessage
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)


async def load_context_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Load recent conversation history from DB.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing ``db``.

    Returns:
        Dict with ``history_messages``.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, returning empty history")
        return {"history_messages": []}

    settings = get_settings()
    history_turns = settings.conversation.history_turns
    limit = history_turns * 2

    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == state["conversation_id"],
            ConversationMessage.tenant_id == state["tenant_id"],
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    messages = list(reversed(messages))

    return {
        "history_messages": [{"role": m.role, "content": m.content} for m in messages],
    }
