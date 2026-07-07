"""Tests for the generation node."""
import pytest
from langgraph.runtime import Runtime
from sales_agent.graph.chat.nodes.generation import generate_node
from sales_agent.graph.chat.state import ChatGraphState


@pytest.mark.asyncio
async def test_generate_node_returns_answer_dict():
    """Generation node produces a structured answer when no model in context."""
    runtime = Runtime(context={})
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户说太贵了怎么回", "conversation_id": "c1", "channel": "local",
        "task_type": "objection_handling",
    }
    result = await generate_node(state, runtime)
    assert "answer_dict" in result
    assert "summary" in result["answer_dict"]
    assert result["answer_dict"]["summary"].startswith("No model available")


@pytest.mark.asyncio
async def test_generate_node_returns_usage():
    """Generation node includes token usage."""
    runtime = Runtime(context={})
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "hello", "conversation_id": "c1", "channel": "local",
        "task_type": "general_sales_coaching",
    }
    result = await generate_node(state, runtime)
    assert "usage" in result
    assert "total_tokens" in result["usage"]


@pytest.mark.asyncio
async def test_generate_node_skip_generation():
    """Generation node is a no-op when skip_generation is set."""
    runtime = Runtime(context={})
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "hello", "conversation_id": "c1", "channel": "local",
        "skip_generation": True,
    }
    result = await generate_node(state, runtime)
    assert result == {}


@pytest.mark.asyncio
async def test_generate_node_passes_through_sources():
    """generate_node 把 state.sources 透传进 answer_dict（no_model fallback 路径）。"""
    runtime = Runtime(context={})
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "hello", "conversation_id": "c1", "channel": "local",
        "task_type": "general_sales_coaching",
        "sources": [{"title": "S1", "source_type": "ontology"}],
    }
    result = await generate_node(state, runtime)
    assert result["answer_dict"]["sources"] == [{"title": "S1", "source_type": "ontology"}]
