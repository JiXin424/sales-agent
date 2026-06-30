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
from sales_agent.graph.nodes.tenant_resolve import resolve_tenant_node
from sales_agent.graph.nodes.context_load import load_context_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.nodes.retrieval import retrieve_node
from sales_agent.graph.edges.path_conditions import is_fast_command, select_retrieval_path


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Phase 2 graph structure::

        START ──(is_fast_command?)──→ fast_reply ──→ END
                    │
                    ▼
              validate ──→ resolve_tenant ──→ load_context
                                                    │
                                                    ▼
                                              route_task
                                                    │
                                         ┌──────────┴──────────┐
                                         ▼                     ▼
                                    [retrieval]            [generate]
                                    (Phase 3)                  │
                                                              ▼
                                                             END

    Caller is responsible for calling ``.compile()`` with the appropriate
    checkpointer and other runtime options.

    Returns:
        A StateGraph builder ready for node/edge registration.
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node("retrieve", retrieve_node)       # NEW
    builder.add_node("generate", generate_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START,
        is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")
    # route_task -> determine retrieval path -> retrieve or skip
    builder.add_conditional_edges(
        "route_task",
        select_retrieval_path,
        {"ontology": "retrieve", "rag": "retrieve", "skip": "generate"},
    )

    # retrieve -> generate (ontology path sets skip_generation internally)
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)

    return builder
