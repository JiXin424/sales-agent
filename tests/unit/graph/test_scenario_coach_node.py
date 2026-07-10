"""Tests for scenario_coach_node and log_scenario_response_node."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_agent.graph.online.nodes import (
    log_scenario_response_node,
    scenario_coach_node,
)
from sales_agent.scenarios.models import ScenarioMatchDecision


def _build_config(context: dict) -> dict:
    return {"configurable": {"__pregel_runtime": SimpleNamespace(context=context)}}


def _base_state(**overrides) -> dict:
    state = {
        "tenant_id": "t1",
        "agent_id": "a1",
        "user_id": "u1",
        "channel": "dingtalk",
        "conversation_id": "c1",
        "message": "客户说别家更便宜怎么办",
        "event_id": "ev-1",
        "flow_action": "chat",
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_scenario_node_hit_sets_answer_dict_with_source():
    override = AsyncMock(
        return_value=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.9, reason_code="price_objection"
        )
    )
    cfg = _build_config({"chat_model": None, "db": None, "scenario_matcher_override": override})
    result = await scenario_coach_node(_base_state(), cfg)
    assert result["response_kind"] == "scenario"
    answer = result["answer_dict"]
    assert answer["summary"]
    assert isinstance(answer["sections"], list) and answer["sections"]
    # section contract: title + content
    for s in answer["sections"]:
        assert "title" in s and "content" in s
    # source citation: manual name + scenario_coach type
    assert answer["sources"] == [
        {
            "title": "销冠智慧教练手册·2026年4月版",
            "display_title": "销冠智慧教练手册·2026年4月版",
            "source_type": "scenario_coach",
        }
    ]
    assert result["last_event_id"] == "ev-1"


@pytest.mark.asyncio
async def test_scenario_node_miss_passthrough():
    override = AsyncMock(
        return_value=ScenarioMatchDecision(
            matched_question_id=None, confidence=0.1, reason_code="irrelevant"
        )
    )
    cfg = _build_config({"chat_model": None, "db": None, "scenario_matcher_override": override})
    result = await scenario_coach_node(_base_state(), cfg)
    # Miss MUST reset response_kind/answer_dict to None (not omit them) so a
    # stale "scenario" kind from a prior turn's hit cannot leak across turns
    # and misroute route_after_scenario. See test_hit_then_miss_same_thread_no_leak.
    assert result["response_kind"] is None
    assert result["answer_dict"] is None
    assert result["last_event_id"] == "ev-1"
    assert result["response_kind"] != "scenario"


@pytest.mark.asyncio
async def test_log_scenario_response_node_persists():
    log_mock = AsyncMock()
    # Patch conversation_logger.log_conversation for the test.
    import sales_agent.graph.online.nodes as nodes_mod

    orig = nodes_mod.conversation_logger.log_conversation
    nodes_mod.conversation_logger.log_conversation = log_mock
    try:
        cfg = _build_config({"db": object()})
        state = _base_state(
            answer_dict={"summary": "s", "sections": [], "sources": []},
            original_message="客户嫌贵",
        )
        result = await log_scenario_response_node(state, cfg)
        assert result == {"last_event_id": "ev-1"}
        log_mock.assert_awaited_once()
        _, kwargs = log_mock.call_args
        assert kwargs["task_type"] == "scenario"
        assert kwargs["path"] == "scenario"
        assert kwargs["message"] == "客户嫌贵"
    finally:
        nodes_mod.conversation_logger.log_conversation = orig


@pytest.mark.asyncio
async def test_log_scenario_response_node_no_db_is_noop():
    cfg = _build_config({"db": None})
    result = await log_scenario_response_node(_base_state(), cfg)
    assert result == {"last_event_id": "ev-1"}
