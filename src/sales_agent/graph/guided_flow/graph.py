"""One-turn Guided Flow subgraph.

Routes on ``flow_action``:

- ``"start"`` → ``start_flow_node``
- ``"advance"`` → ``advance_flow_node``
- ``"cancel"`` → ``cancel_flow_node``

All three edges terminate at ``END`` after a single node execution (hence
*one-turn*).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sales_agent.graph.guided_flow.nodes import (
    advance_flow_node,
    cancel_flow_node,
    start_flow_node,
)
from sales_agent.graph.guided_flow.state import GuidedFlowState


def _route_action(state: GuidedFlowState) -> str:
    return state.get("flow_action", "advance")


def build_guided_flow_graph() -> StateGraph:
    """Return an **uncompiled** :class:`StateGraph` for guided flows.

    The caller is expected to call ``.compile(checkpointer=…)`` and then
    ``.ainvoke(input, config=config, context=…)``.
    """
    builder = StateGraph(GuidedFlowState)

    builder.add_node("start_flow", start_flow_node)
    builder.add_node("advance_flow", advance_flow_node)
    builder.add_node("cancel_flow", cancel_flow_node)

    builder.add_conditional_edges(
        START,
        _route_action,
        {"start": "start_flow", "advance": "advance_flow", "cancel": "cancel_flow"},
    )

    builder.add_edge("start_flow", END)
    builder.add_edge("advance_flow", END)
    builder.add_edge("cancel_flow", END)

    return builder


__all__ = [
    "build_guided_flow_graph",
]
