"""Tests for the routing node."""
import pytest
from unittest.mock import MagicMock
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import (
    GENERAL_COACHING, KNOWLEDGE_QA, OBJECTION_HANDLING,
    SCRIPT_GENERATION, EMOTIONAL_SUPPORT, CONVERSATION_REVIEW,
    VISIT_PREPARATION, POST_VISIT_REVIEW,
)


@pytest.fixture
def mock_runtime():
    """Runtime with no chat_model — flag defaults off, rule path used."""
    runtime = MagicMock()
    runtime.context = {"chat_model": None, "db": None}
    return runtime


@pytest.mark.asyncio
async def test_routing_objection_handling(mock_runtime):
    """Messages about pricing trigger objection_handling."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户说太贵了怎么回", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == OBJECTION_HANDLING
    assert result["route_confidence"] > 0.5


@pytest.mark.asyncio
async def test_routing_knowledge_qa(mock_runtime):
    """Product questions trigger knowledge_qa."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == KNOWLEDGE_QA


@pytest.mark.asyncio
async def test_routing_script_generation(mock_runtime):
    """Message writing requests trigger script_generation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我写一段跟进话术", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == SCRIPT_GENERATION


@pytest.mark.asyncio
async def test_routing_emotional_support(mock_runtime):
    """Frustration expressions trigger emotional_support."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户一直不回，我觉得很沮丧", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == EMOTIONAL_SUPPORT


@pytest.mark.asyncio
async def test_routing_conversation_review(mock_runtime):
    """Chat analysis triggers conversation_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我复盘这段聊天记录", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == CONVERSATION_REVIEW


@pytest.mark.asyncio
async def test_routing_visit_preparation(mock_runtime):
    """Visit prep triggers visit_preparation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "明天拜访客户帮我准备拜访提纲", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == VISIT_PREPARATION


@pytest.mark.asyncio
async def test_routing_post_visit_review(mock_runtime):
    """Post-visit triggers post_visit_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "刚聊完客户帮我做一个访后复盘", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == POST_VISIT_REVIEW


@pytest.mark.asyncio
async def test_routing_general_fallback(mock_runtime):
    """Unrecognized messages fall back to general_sales_coaching."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "你好", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert result["task_type"] == GENERAL_COACHING


@pytest.mark.asyncio
async def test_routing_returns_needs_retrieval(mock_runtime):
    """Routing sets needs_retrieval flag."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = await routing_node(state, mock_runtime)
    assert "needs_retrieval" in result
