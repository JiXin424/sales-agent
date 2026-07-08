"""Tests for the scenario matcher."""

from __future__ import annotations

import pytest

from sales_agent.scenarios.matcher import match_scenario
from sales_agent.scenarios.models import ScenarioMatchDecision


class _FakeChatModel:
    """Mirrors test_evidence_router._FakeChatModel."""

    def __init__(self, response: str):
        self._response = response
        self.received_messages: list[dict] | None = None

    async def generate(self, messages, **kwargs):
        self.received_messages = messages
        return self._response


class _BoomChatModel:
    """Always raises — simulates LLM/network failure."""

    async def generate(self, messages, **kwargs):
        raise RuntimeError("boom")


def _q_json_sent(model: _FakeChatModel) -> bool:
    """True if the system prompt contained the preset question list (e.g. Q01)."""
    assert model.received_messages is not None
    system = model.received_messages[0]["content"]
    return "Q01" in system and "预设场景问题列表" in system


@pytest.mark.asyncio
async def test_match_above_threshold():
    model = _FakeChatModel(
        '{"matched_question_id":"Q01","confidence":0.9,"reason_code":"price_objection"}'
    )
    decision = await match_scenario(
        "客户说别家更便宜怎么办", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id == "Q01"
    assert decision.confidence == 0.9
    assert _q_json_sent(model)


@pytest.mark.asyncio
async def test_no_match_irrelevant():
    model = _FakeChatModel(
        '{"matched_question_id":null,"confidence":0.1,"reason_code":"irrelevant"}'
    )
    decision = await match_scenario(
        "今天天气真好", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None


@pytest.mark.asyncio
async def test_below_threshold_returns_none():
    model = _FakeChatModel(
        '{"matched_question_id":"Q01","confidence":0.6,"reason_code":"price_objection"}'
    )
    decision = await match_scenario(
        "客户嫌贵", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "below_threshold"


@pytest.mark.asyncio
async def test_unknown_question_id_returns_none():
    model = _FakeChatModel(
        '{"matched_question_id":"Q99","confidence":0.95,"reason_code":"x"}'
    )
    decision = await match_scenario(
        "whatever", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "unknown_question_id"


@pytest.mark.asyncio
async def test_invalid_json_retries_then_failopen():
    model = _FakeChatModel("not valid json at all")
    decision = await match_scenario(
        "客户嫌贵", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "parse_failure"


@pytest.mark.asyncio
async def test_llm_failure_failopen():
    decision = await match_scenario(
        "客户嫌贵", chat_model=_BoomChatModel(), confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "llm_failure"


@pytest.mark.asyncio
async def test_empty_message_no_llm_call():
    model = _FakeChatModel('{"matched_question_id":"Q01","confidence":0.99}')
    decision = await match_scenario(
        "   ", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "empty_message"
    assert model.received_messages is None  # LLM never called
