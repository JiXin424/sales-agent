"""Tests for ChatGraphState."""
import pytest
from sales_agent.graph.chat.state import (
    ChatGraphState,
    _reduce_coalesce,
    _reduce_merge_dict,
    _reduce_or,
)


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


class TestReduceCoalesce:
    """``_reduce_coalesce`` — 单值字段在并行 Send fan-out 下的合并策略。

    覆盖 ``ontology_context_text`` / ``retrieval_result`` 两路同时写场景，
    防止 ``InvalidUpdateError``（commit eb889df 同类问题）。
    """

    def test_none_none_returns_empty(self):
        assert _reduce_coalesce(None, None) is None

    def test_first_value_wins_when_second_empty(self):
        """ontology 路有值、rag 路空 → 取 ontology 值。"""
        assert _reduce_coalesce("## 本体结果", "") == "## 本体结果"
        assert _reduce_coalesce("## 本体结果", None) == "## 本体结果"

    def test_second_value_wins_when_first_empty(self):
        """ontology 路空、rag 路 web_fallback 有值 → 取 rag 值。"""
        assert _reduce_coalesce("", "## 联网分析") == "## 联网分析"
        assert _reduce_coalesce(None, "## 联网分析") == "## 联网分析"

    def test_both_nonempty_keeps_first(self):
        """两路都有值（都 web_fallback，内容重复）→ 取先到者，避免冗余拼接。"""
        a, b = "## 本体结果", "## 联网分析"
        result = _reduce_coalesce(a, b)
        assert result in (a, b)
        assert result == a  # 实现语义：非空 a 优先

    def test_object_coalesce_for_retrieval_result(self):
        """retrieval_result 是任意对象，None/空容器视为缺席。"""
        obj = {"chunks": [1, 2]}
        assert _reduce_coalesce(None, obj) is obj
        assert _reduce_coalesce(obj, None) is obj
        assert _reduce_coalesce(obj, {"other": 1}) is obj
