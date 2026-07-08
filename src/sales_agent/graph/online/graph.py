"""Unified online conversation graph — entry point for all message processing.

Routes every incoming message to either:

- The **Guided Flow** subgraph (start / advance / cancel)
- The **Chat** graph (existing pipeline)
- A **duplicate** handler

Routing priority (highest first):

1. Duplicate event (same ``event_id`` as ``last_event_id``)
2. ``guided_flows_enabled`` & a ``requested_flow`` trigger  →  ``"start"``
3. ``guided_flows_enabled`` & ``active_flow`` & cancel command →  ``"cancel"``
4. ``guided_flows_enabled`` & ``active_flow`` →  ``"advance"``
5. Otherwise →  ``"chat"``

File layout::

    online/
    ├── graph.py   ← this file:  graph builder only
    ├── nodes.py   ← all 8 node functions + subgraph factories
    ├── edges.py   ← route_online_message, route_context_resolution
    └── state.py   ← OnlineConversationState

Subgraph factories (``_get_guided_flow_graph`` / ``_get_chat_graph``) live
in ``nodes.py`` to avoid a circular import with the ``chat_node`` function.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sales_agent.graph.online.edges import route_context_resolution, route_online_message
from sales_agent.graph.online.nodes import (
    _get_guided_flow_graph,
    chat_node,
    clarification_response_node,
    context_resolution_node,
    direct_evidence_routing_node,
    duplicate_node,
    evidence_routing_node,
    log_control_response_node,
    log_flow_output_node,
    normalize_turn_node,
)
from sales_agent.graph.online.state import OnlineConversationState


def build_online_graph() -> StateGraph:
    """Build the unified online conversation graph.

    Structure::

        START  →  normalize_turn  →  [flow_action]
                         │
            ┌────────────┼──────────────────────────────┐
            v            v                              v
        duplicate    guided_flow                  context_resolution
                         │                              │
                         v                    ┌─────────┴──────────┐
                   log_flow_output            v                   v
                         │           clarification       evidence_routing
            └────────────┴───────────    response                 │
                         v               │                      v
                        END              v                     chat
                                   log_control                    │
                                       │                         v
                                       └─────────┬───────────────┘
                                                 v
                                                END

    Returns:
        An uncompiled :class:`StateGraph`.  The caller must call
        ``.compile(checkpointer=…)`` before invoking.
    """
    builder = StateGraph(OnlineConversationState)

    # ── Nodes ──────────────────────────────────────────────────────
    builder.add_node("normalize_turn", normalize_turn_node)
    builder.add_node("guided_flow", _get_guided_flow_graph())
    builder.add_node("context_resolution", context_resolution_node)
    builder.add_node("evidence_routing", evidence_routing_node)
    builder.add_node("direct_evidence_routing", direct_evidence_routing_node)
    builder.add_node("clarification_response", clarification_response_node)
    builder.add_node("log_control_response", log_control_response_node)
    builder.add_node("chat", chat_node)
    builder.add_node("duplicate", duplicate_node)
    builder.add_node("log_flow_output", log_flow_output_node)

    # ── Edges ──────────────────────────────────────────────────────
    builder.add_edge(START, "normalize_turn")

    # From normalize_turn: route according to flow_action
    builder.add_conditional_edges(
        "normalize_turn",
        route_online_message,
        {
            "duplicate": "duplicate",
            "start": "guided_flow",
            "cancel": "guided_flow",
            "advance": "guided_flow",
            "chat": "context_resolution",
            "direct_chat": "direct_evidence_routing",
        },
    )

    # From context_resolution: clarify, resolved, control, or cancel
    builder.add_conditional_edges(
        "context_resolution",
        route_context_resolution,
        {
            "clarify": "clarification_response",
            "resolved": "evidence_routing",
            "control": "log_control_response",
            "cancel": END,
        },
    )

    # Clarification path
    builder.add_edge("clarification_response", "log_control_response")
    builder.add_edge("log_control_response", END)

    # Resolved path
    builder.add_edge("evidence_routing", "chat")
    # Direct-chat path (topic_routing off): still classify intent so knowledge
    # questions retrieve; bypasses context_resolution/clarify only.
    builder.add_edge("direct_evidence_routing", "chat")
    builder.add_edge("chat", END)

    # Guided flow path
    builder.add_edge("guided_flow", "log_flow_output")
    builder.add_edge("log_flow_output", END)
    builder.add_edge("duplicate", END)

    return builder


__all__ = [
    "build_online_graph",
]
