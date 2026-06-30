"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic `ChatPipeline.execute()` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.nodes.tenant_resolve import resolve_tenant_node
from sales_agent.graph.nodes.context_load import load_context_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.nodes.retrieval import retrieve_node
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.nodes.risk_check import risk_check_node
from sales_agent.graph.nodes.logging_node import log_node
from sales_agent.graph.edges.path_conditions import is_fast_command, select_retrieval_path
from sales_agent.graph.edges.risk_conditions import check_risk_result
from sales_agent.graph.retry_policies import LLM_RETRY_POLICY, LLM_TIMEOUT


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Phase 3 graph structure::

        START --(is_fast_command?)--> fast_reply --> END
                    |
                    v
              validate --> resolve_tenant --> load_context
                                                    |
                                                    v
                                              route_task
                                                    |
                                         +----------+----------+
                                         v          v          v
                                     ontology      rag        skip
                                         |          |          |
                                         +----------+----------+
                                                    |
                                                generate  <---------------+
                                                    |                      |
                                              check_risk                   |
                                                    |                      |
                                         +----------+----------+           |
                                         v          v          v           |
                                       pass       block     rewrite        |
                                         |          |          |           |
                                         v          +----------+-----------+
                                       log                         (loop: retry_count++)
                                         |
                                         v
                                        END
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node(
        "generate",
        generate_node,
        retry_policy=LLM_RETRY_POLICY,
        timeout=LLM_TIMEOUT,
    )
    builder.add_node("check_risk", risk_check_node)
    builder.add_node("log", log_node)

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
    builder.add_edge("generate", "check_risk")
    builder.add_conditional_edges(
        "check_risk",
        check_risk_result,
        {
            "pass": "log",
            "block": "generate",       # CYCLE: regenerate with safety notice
            "rewrite": "generate",     # CYCLE: regenerate with rewrite hint
            "max_retries": "log",      # give up after 3 retries
        },
    )
    builder.add_edge("log", END)

    return builder
