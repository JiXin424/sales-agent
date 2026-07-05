"""Tests for structured router output contracts and JSON parsing."""

import pytest
from pydantic import ValidationError

from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
    EvidenceDecision,
    parse_model_json,
)


def test_parse_fenced_json():
    result = parse_model_json(
        '```json\n{"turn_relation":"new","standalone_query":"你好","confidence":0.9,"reason_code":"new_topic"}\n```',
        ContextDecision,
    )
    assert result.turn_relation == "new"


def test_unknown_relation_is_rejected():
    with pytest.raises(ValidationError):
        ContextDecision(
            turn_relation="maybe", standalone_query="x", confidence=0.5, reason_code="x"
        )


def test_required_policy_requires_retrieval_query():
    with pytest.raises(ValueError):
        EvidenceDecision(
            intent="knowledge_qa",
            response_mode="retrieve",
            knowledge_policy="required",
            knowledge_scope=["product"],
            retrieval_query=None,
            confidence=0.9,
            reason_code="fact",
        )


def test_clarification_enum_is_closed():
    decision = ClarificationDecision(
        resolution="continue", supplemental_message="重点看价格", confidence=0.9
    )
    assert decision.resolution == "continue"


def test_parse_bare_json():
    """Bare JSON (no fences) is parsed correctly."""
    result = parse_model_json(
        '{"turn_relation":"new","standalone_query":"hello","confidence":0.8,"reason_code":"new_topic"}',
        ContextDecision,
    )
    assert result.turn_relation == "new"
    assert result.standalone_query == "hello"


def test_parse_malformed_json_with_repair():
    """Malformed JSON falls back to json_repair successfully."""
    result = parse_model_json(
        "{turn_relation: 'new', standalone_query: 'hello', confidence: 0.8, reason_code: 'new_topic'}",
        ContextDecision,
    )
    assert result.turn_relation == "new"
