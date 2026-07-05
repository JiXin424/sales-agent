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

    When ``precomputed_route=True`` (set by Online Graph's evidence routing),
    return existing ``task_type``, ``needs_retrieval``, and
    ``knowledge_policy`` fields without calling ``route_task`` — the upstream
    router has already made its decision.

    Otherwise, uses rules-only matching from the existing ``task_router`` module.
    LLM fallback is deferred to Phase 3 (requires Runtime.context).

    Args:
        state: Current graph state with ``message`` populated.

    Returns:
        Dict with ``task_type``, ``route_confidence``, ``needs_retrieval``,
        and optionally ``needs_clarification`` or ``knowledge_policy``.
    """
    if state.get("precomputed_route"):
        return {
            "task_type": state.get("task_type"),
            "needs_retrieval": state.get("needs_retrieval", False),
            "knowledge_policy": state.get("knowledge_policy"),
            "route_confidence": state.get("route_confidence", 0.5),
        }

    message = state["message"]

    route_result = route_task_rules_only(message)

    return {
        "task_type": route_result.task_type,
        "route_confidence": route_result.confidence,
        "needs_retrieval": route_result.needs_retrieval,
        "needs_clarification": route_result.needs_clarification,
    }
