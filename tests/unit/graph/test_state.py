"""Tests for ChatGraphState."""
import pytest
from sales_agent.graph.state import ChatGraphState


def test_state_instantiation_minimal():
    """ChatGraphState can be instantiated with minimal required fields."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "客户说太贵了怎么回",
        "conversation_id": "conv-001",
        "channel": "local",
    }
    assert state["tenant_id"] == "t1"
    assert state["message"] == "客户说太贵了怎么回"


def test_state_instantiation_all_fields():
    """ChatGraphState accepts all defined optional fields."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "hello",
        "conversation_id": "c1",
        "channel": "dingtalk",
        "agent_id": "a1",
        "model_override": None,
        "task_type": "general_sales_coaching",
        "route_confidence": 0.9,
        "needs_retrieval": False,
        "path": "fast",
        "path_reason": "simple_command",
        "answer_dict": {"summary": "ok", "sections": []},
        "sources": [],
        "risk_action": "allow",
        "risk_result": {},
        "run_id": None,
        "usage": {},
        "error": None,
        "retry_count": 0,
    }
    assert state["task_type"] == "general_sales_coaching"
    assert state["retry_count"] == 0


def test_state_optional_fields_default():
    """Optional fields can be omitted."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "test",
        "conversation_id": "c1",
        "channel": "local",
    }
    # Optional fields should be accessible without error
    assert state.get("task_type") is None
    assert state.get("retry_count", 0) == 0
