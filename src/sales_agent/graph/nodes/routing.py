"""Task routing node.

Calls the existing ``task_router.route_task_rules_only()`` service — a
rules-first approach without LLM fallback. LLM-based routing is deferred
to Phase 3 when the chat_model is available via Runtime.context.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import route_task_rules_only


def routing_node(state: ChatGraphState) -> dict:
    """Route the user message to a task type.

    Uses rules-only matching from the existing ``task_router`` module.
    LLM fallback is deferred to Phase 3 (requires Runtime.context).

    Args:
        state: Current graph state with ``message`` populated.

    Returns:
        Dict with ``task_type``, ``route_confidence``, ``needs_retrieval``,
        and optionally ``needs_clarification``.
    """
    message = state["message"]

    route_result = route_task_rules_only(message)

    return {
        "task_type": route_result.task_type,
        "route_confidence": route_result.confidence,
        "needs_retrieval": route_result.needs_retrieval,
        "needs_clarification": route_result.needs_clarification,
    }
