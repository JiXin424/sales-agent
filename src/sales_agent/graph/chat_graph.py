"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic ``ChatPipeline.execute()`` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.

LangGraph features integrated:
- P0: HITL via ``interrupt()``, ``interrupt_before/after``, ``Store``
- P1: ``stream_mode="custom"`` via ``StreamWriter``, ``CachePolicy``,
      ``InjectedState/Store``, ``ToolRuntime``
- P2: ``set_node_defaults``, ``Send`` fan-out, ``Durability``,
      ``TAG_HIDDEN``, ``ValidationNode``
- P3: ``IsLastStep`` managed value, ``REMOVE_ALL_MESSAGES``,

Graph structure::

    START --(is_fast_command?)--> fast_reply --> END
                |
                v
          [validate → resolve_tenant → load_context → route_task]
                |
     +----------+----------+
     v          v          v
  ontology     rag        skip        ← Send fan-out when parallel_enabled
     |          |          |
     +----------+----------+
                |
            generate  <──────┐
                |            |
          check_risk         |
                |            |
     +----------+-----+      |
     v         v       v     |
    pass     block   rewrite |
     |         |       |     |
     v         +───────+─────┘
    log       (loop: retry / interrupt)
     |
     v
    END
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.constants import TAG_HIDDEN

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
from sales_agent.graph.retry_policies import (
    LLM_RETRY_POLICY, LLM_TIMEOUT,
    DB_RETRY_POLICY,
    RETRIEVAL_RETRY_POLICY, RETRIEVAL_TIMEOUT,
    CACHE_RETRIEVAL, CACHE_GENERATION,
)


def build_chat_graph(
    *,
    interrupt_before_nodes: list[str] | None = None,
    interrupt_after_nodes: list[str] | None = None,
) -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Args:
        interrupt_before_nodes: Node names to pause before (HITL).
        interrupt_after_nodes: Node names to pause after (HITL).

    Returns:
        An un-compiled StateGraph ready for ``.compile()``.
    """
    builder = StateGraph(ChatGraphState)

    # ── P2: Global node defaults ───────────────────────────────────
    builder.set_node_defaults(retry_policy=DB_RETRY_POLICY)

    # ── Nodes ──────────────────────────────────────────────────────
    # P2: TAG_HIDDEN on infra-only nodes — invisible to tracing
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node, tags=[TAG_HIDDEN])
    builder.add_node("resolve_tenant", resolve_tenant_node, tags=[TAG_HIDDEN])
    builder.add_node("load_context", load_context_node, tags=[TAG_HIDDEN])
    builder.add_node("route_task", routing_node)

    # P1: Retrieval with cache + retry + timeout
    builder.add_node(
        "retrieve",
        retrieve_node,
        retry_policy=RETRIEVAL_RETRY_POLICY,
        timeout=RETRIEVAL_TIMEOUT,
        cache_policy=CACHE_RETRIEVAL,
    )

    # P1: Generation with cache + retry + timeout
    builder.add_node(
        "generate",
        generate_node,
        retry_policy=LLM_RETRY_POLICY,
        timeout=LLM_TIMEOUT,
        cache_policy=CACHE_GENERATION,
    )
    builder.add_node("check_risk", risk_check_node)
    builder.add_node("log", log_node, tags=[TAG_HIDDEN])

    # ── Edges ──────────────────────────────────────────────────────
    builder.add_conditional_edges(
        START,
        is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)

    # Linear chain
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")

    # route_task → retrieve or skip (supports Send fan-out via select_retrieval_path)
    builder.add_conditional_edges(
        "route_task",
        select_retrieval_path,
        {
            "ontology": "retrieve",
            "rag": "retrieve",
            "skip": "generate",
        },
    )

    # retrieve → generate
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", "check_risk")

    # Risk result branching
    builder.add_conditional_edges(
        "check_risk",
        check_risk_result,
        {
            "pass": "log",
            "block": "generate",
            "rewrite": "generate",
            "human_review": "log",
            "max_retries": "log",
        },
    )
    builder.add_edge("log", END)

    return builder


def build_chat_graph_compiled(
    *,
    checkpointer=None,
    store=None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
):
    """Build and compile the ChatPipeline graph with all options.

    Args:
        checkpointer: LangGraph checkpointer.
        store: LangGraph Store for cross-session memory.
        interrupt_before: Node names to pause before.
        interrupt_after: Node names to pause after.

    Returns:
        A compiled StateGraph ready for invoke/stream/astream.
        Use ``durability`` parameter on the invoke/astream call itself
        to control checkpoint write strategy (P2).
    """
    builder = build_chat_graph(
        interrupt_before_nodes=interrupt_before,
        interrupt_after_nodes=interrupt_after,
    )
    return builder.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=interrupt_before or [],
        interrupt_after=interrupt_after or [],
    )


# ── Graph Debug Registry (auto-discovery for frontend) ────────────
# Add new entries here when adding new subgraphs. The frontend
# Graph Debug page auto-discovers them via GET /graph-debug/graphs.

from sales_agent.graph.coach.daily_eval_graph import build_daily_eval_graph
from sales_agent.graph.coach.quick_session_graph import build_quick_session_graph
from sales_agent.graph.retrieval.ontology_graph import build_ontology_retrieval_graph

DEBUG_GRAPH_REGISTRY: dict[str, dict] = {
    "chat": {
        "name": "Chat Pipeline",
        "builder": build_chat_graph,
    },
    "daily-eval": {
        "name": "Daily Evaluation",
        "builder": build_daily_eval_graph,
    },
    "quick-session": {
        "name": "Quick Session",
        "builder": build_quick_session_graph,
    },
    "ontology-retrieval": {
        "name": "Ontology Retrieval",
        "builder": build_ontology_retrieval_graph,
    },
}
