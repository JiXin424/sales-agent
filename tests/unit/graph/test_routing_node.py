"""Tests for the routing node."""
import pytest
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import (
    GENERAL_COACHING, KNOWLEDGE_QA, OBJECTION_HANDLING,
    SCRIPT_GENERATION, EMOTIONAL_SUPPORT, CONVERSATION_REVIEW,
    VISIT_PREPARATION, POST_VISIT_REVIEW,
)


def test_routing_objection_handling():
    """Messages about pricing trigger objection_handling."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户说太贵了怎么回", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == OBJECTION_HANDLING
    assert result["route_confidence"] > 0.5


def test_routing_knowledge_qa():
    """Product questions trigger knowledge_qa."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == KNOWLEDGE_QA


def test_routing_script_generation():
    """Message writing requests trigger script_generation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我写一段跟进话术", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == SCRIPT_GENERATION


def test_routing_emotional_support():
    """Frustration expressions trigger emotional_support."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户一直不回，我觉得很沮丧", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == EMOTIONAL_SUPPORT


def test_routing_conversation_review():
    """Chat analysis triggers conversation_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我复盘这段聊天记录", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == CONVERSATION_REVIEW


def test_routing_visit_preparation():
    """Visit prep triggers visit_preparation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "明天拜访客户帮我准备拜访提纲", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == VISIT_PREPARATION


def test_routing_post_visit_review():
    """Post-visit triggers post_visit_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "刚聊完客户帮我做一个访后复盘", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == POST_VISIT_REVIEW


def test_routing_general_fallback():
    """Unrecognized messages fall back to general_sales_coaching."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "你好", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == GENERAL_COACHING


def test_routing_returns_needs_retrieval():
    """Routing sets needs_retrieval flag."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert "needs_retrieval" in result
