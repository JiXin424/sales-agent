"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic `ChatPipeline.execute()` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.edges.path_conditions import is_fast_command


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Caller is responsible for calling `.compile()` with the appropriate
    checkpointer and other runtime options.

    Returns:
        A StateGraph builder ready for node/edge registration.
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("route_task", routing_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START,
        is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "route_task")
    builder.add_edge("route_task", END)  # Phase 1: short-circuit

    return builder
