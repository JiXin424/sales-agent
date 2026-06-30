"""Conversation logging node.

Persists the completed conversation turn and records latency stats.
"""

from __future__ import annotations

import logging
from langgraph.runtime import Runtime
from sales_agent.graph.state import ChatGraphState
from sales_agent.services import conversation_logger

logger = logging.getLogger(__name__)


async def log_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Log the completed conversation turn to the database.

    Args:
        state: Current graph state with all results populated.
        runtime: LangGraph runtime with context containing ``db``.

    Returns:
        Empty dict — this is a terminal node.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, skipping logging")
        return {}

    try:
        await conversation_logger.log_conversation(
            db,
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            channel=state.get("channel", "local"),
            agent_id=state.get("agent_id"),
            conversation_id=state["conversation_id"],
            message=state["message"],
            task_type=state.get("task_type", "general_sales_coaching"),
            task_confidence=state.get("route_confidence", 0.5),
            answer_dict=state.get("answer_dict", {}),
            risk_dict=state.get("risk_result", {}),
            sources=state.get("sources", []),
            model_config={},
            status="completed",
            stage_latency_ms={},
            path=state.get("path", "standard"),
            path_reason=state.get("path_reason", ""),
        )
    except Exception as e:
        logger.warning("Failed to log conversation: %s", e)

    return {}
