"""Tests for the ChatPipeline StateGraph."""
import pytest
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.state import ChatGraphState
from langgraph.graph.state import StateGraph


def test_build_chat_graph_returns_state_graph():
    """build_chat_graph returns an un-compiled StateGraph builder."""
    builder = build_chat_graph()
    assert isinstance(builder, StateGraph)


def test_graph_compiles_without_checkpointer():
    """Graph compiles successfully without a checkpointer (for testing)."""
    builder = build_chat_graph()
    graph = builder.compile()
    assert graph is not None


def test_graph_compiles_with_memory_checkpointer():
    """Graph compiles with InMemorySaver for unit testing."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_invokes_minimal_input():
    """Graph processes minimal input through basic pipeline."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "帮助",
            "conversation_id": "conv-001",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-1"}},
        context={"db": None},
    )
    # After invoke, state passes through the pipeline
    assert result is not None
    assert result["message"] == "帮助"
    assert result["tenant_id"] == "t1"
