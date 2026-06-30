"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic `ChatPipeline.execute()` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Caller is responsible for calling `.compile()` with the appropriate
    checkpointer and other runtime options.

    Returns:
        A StateGraph builder ready for node/edge registration.
    """
    builder = StateGraph(ChatGraphState)

    # Phase 1: register nodes (stubs that pass-through for now)

    # Phase 1: edges — validate → END (minimal path to test compilation)
    builder.add_edge(START, END)

    return builder
