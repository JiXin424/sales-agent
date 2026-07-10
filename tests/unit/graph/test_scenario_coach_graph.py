"""Tests for scenario_coach graph wiring (state flag + input_state coverage)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from unittest.mock import AsyncMock

from sales_agent.graph.online.edges import route_online_message
from sales_agent.graph.online.graph import build_online_graph
from sales_agent.scenarios.models import ScenarioMatchDecision
from sales_agent.services.structured_router_output import EvidenceDecision


def test_route_online_message_disabled_passes_through():
    """When scenario_coach_enabled is False/absent, routing is unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": False}) == "chat"
    assert route_online_message({"flow_action": "direct_chat"}) == "direct_chat"
    assert route_online_message({"flow_action": "duplicate"}) == "duplicate"
    assert route_online_message({}) == "chat"  # default


def test_route_online_message_enabled_diverts_chat_paths():
    """When enabled, chat/direct_chat divert to scenario_coach; others unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": True}) == "scenario_coach"
    assert route_online_message({"flow_action": "direct_chat", "scenario_coach_enabled": True}) == "scenario_coach"
    # guided-flow / duplicate paths are NOT intercepted
    assert route_online_message({"flow_action": "start", "scenario_coach_enabled": True}) == "start"
    assert route_online_message({"flow_action": "duplicate", "scenario_coach_enabled": True}) == "duplicate"


def _evidence_override_decision() -> EvidenceDecision:
    """A minimal valid EvidenceDecision so direct_evidence_routing needs no LLM."""
    return EvidenceDecision(
        intent="general_sales_coaching",
        response_mode="direct",
        knowledge_policy="none",
        knowledge_scope=[],
        retrieval_query=None,
        confidence=0.5,
        reason_code="test",
    )


def _runtime_ctx(*, scenario_coach_enabled: bool, matcher_decision, chat_runner=None):
    """Build a runtime context dict with a scenario_matcher override + stub chat.

    evidence_router_override is always injected so the direct_chat miss path
    (direct_evidence_routing_node) does not need a real chat_model.
    """
    ctx = {
        "db": None,
        "chat_model": None,
        "now": None,
        "scenario_matcher_override": AsyncMock(return_value=matcher_decision),
        "evidence_router_override": AsyncMock(return_value=_evidence_override_decision()),
    }
    if chat_runner is not None:
        ctx["chat_runner"] = chat_runner
    return ctx


def _input(message: str, *, scenario_coach_enabled: bool) -> dict:
    return {
        "tenant_id": "t1",
        "agent_id": "a1",
        "user_id": "u1",
        "session_user_id": "u1",
        "channel": "dingtalk",
        "conversation_id": "c-scenario-1",
        "message": message,
        "entry_action": None,
        "event_id": "ev-1",
        "guided_flows_enabled": False,
        "topic_routing_enabled": False,
        "scenario_coach_enabled": scenario_coach_enabled,
    }


class _StubChatRunner:
    """Replaces the Chat subgraph; records that it ran (a 'miss' should reach it)."""

    def __init__(self):
        self.called = False

    async def ainvoke(self, chat_input, config=None, context=None):
        self.called = True
        return {"answer_dict": {"summary": "AI answer", "sections": [], "sources": []}}


@pytest.mark.asyncio
async def test_enabled_hit_returns_preset_and_skips_chat():
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    ctx = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.9, reason_code="price_objection"
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("客户说别家更便宜怎么办", scenario_coach_enabled=True),
        config={"configurable": {"thread_id": "c-scenario-1"}},
        context=ctx,
    )
    assert result["response_kind"] == "scenario"
    assert result["answer_dict"]["summary"]
    assert result["answer_dict"]["sources"][0]["source_type"] == "scenario_coach"
    # Chat subgraph (AI generation) must NOT have run on a hit.
    assert runner.called is False


@pytest.mark.asyncio
async def test_enabled_miss_falls_through_to_chat():
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    ctx = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id=None, confidence=0.1, reason_code="irrelevant"
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("今天天气真好", scenario_coach_enabled=True),
        config={"configurable": {"thread_id": "c-scenario-2"}},
        context=ctx,
    )
    # Miss → normal chat path runs.
    assert runner.called is True
    assert result.get("response_kind") in (None, "chat")
    assert result["answer_dict"]["summary"] == "AI answer"


@pytest.mark.asyncio
async def test_disabled_uses_original_path():
    """Feature off → scenario_coach never entered; chat runs normally (regression)."""
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    # matcher override present but must NOT be consulted when disabled.
    ctx = _runtime_ctx(
        scenario_coach_enabled=False,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.99
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("随便问个问题", scenario_coach_enabled=False),
        config={"configurable": {"thread_id": "c-scenario-3"}},
        context=ctx,
    )
    assert runner.called is True
    # No scenario response.
    assert result.get("response_kind") != "scenario"


@pytest.mark.asyncio
async def test_hit_then_miss_same_thread_no_leak():
    """Regression: response_kind must not leak across turns on the same thread.

    Turn 1 hits a scenario (sets response_kind='scenario'); turn 2 is a clear
    miss on the SAME thread_id and must reach the chat path normally (not be
    misrouted by the stale response_kind).
    """
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    tid = "c-scenario-leak"

    def _turn_input(message, eid):
        return {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "session_user_id": "u1", "channel": "dingtalk",
            "conversation_id": tid, "message": message, "entry_action": None,
            "event_id": eid, "guided_flows_enabled": False,
            "topic_routing_enabled": False, "scenario_coach_enabled": True,
        }

    # Turn 1: HIT
    runner1 = _StubChatRunner()
    ctx1 = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.9, reason_code="price_objection"
        ),
        chat_runner=runner1,
    )
    r1 = await graph.ainvoke(
        _turn_input("客户说别家更便宜怎么办", "ev-leak-1"),
        config={"configurable": {"thread_id": tid}},
        context=ctx1,
    )
    assert r1["response_kind"] == "scenario"
    assert runner1.called is False

    # Turn 2: MISS on the SAME thread, distinct event_id
    runner2 = _StubChatRunner()
    ctx2 = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id=None, confidence=0.1, reason_code="irrelevant"
        ),
        chat_runner=runner2,
    )
    r2 = await graph.ainvoke(
        _turn_input("今天天气真好", "ev-leak-2"),
        config={"configurable": {"thread_id": tid}},
        context=ctx2,
    )
    # Miss on a fresh turn MUST reach chat, not be misrouted by stale response_kind.
    assert runner2.called is True
    assert r2.get("response_kind") != "scenario"
