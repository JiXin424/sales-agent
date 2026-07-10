"""Test Observe: OutcomeExtraction model, fallback parser, and repository write_outcome."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from sales_agent.services.sales_actions.contracts import OUTCOME_TAGS, OutcomeExtraction


def test_outcome_extraction_model():
    ext = OutcomeExtraction(
        outcome_tag="new_obstacle",
        outcome_note="他说最近预算冻结",
        met_signal=False,
        confidence=0.92,
    )
    assert ext.outcome_tag in OUTCOME_TAGS
    assert ext.met_signal is False
    assert ext.confidence == 0.92
    assert ext.parse_failed is False


def test_outcome_extraction_defaults():
    ext = OutcomeExtraction(
        outcome_tag="achieved",
        confidence=0.85,
    )
    assert ext.outcome_note == ""
    assert ext.met_signal is False
    assert ext.parse_failed is False


def test_outcome_extraction_all_tags():
    for tag in ("achieved", "partial", "new_obstacle", "no_response"):
        ext = OutcomeExtraction(outcome_tag=tag, confidence=0.5)
        assert ext.outcome_tag in OUTCOME_TAGS


def test_fallback_on_invalid_json():
    from sales_agent.services.sales_actions.parser import _fallback_outcome

    result = _fallback_outcome("约到了", confidence=0.5)
    assert result.outcome_tag in OUTCOME_TAGS
    assert result.met_signal is True  # "约到" is positive
    assert result.parse_failed is True
    assert result.confidence == 0.5

    result = _fallback_outcome("他预算冻结了")
    assert result.outcome_tag == "new_obstacle"
    assert result.met_signal is False
    assert result.parse_failed is True


def test_fallback_outcome_partial():
    from sales_agent.services.sales_actions.parser import _fallback_outcome

    result = _fallback_outcome("他回了消息但没给时间")
    assert result.outcome_tag == "partial"
    assert result.met_signal is False


def test_fallback_outcome_no_match():
    from sales_agent.services.sales_actions.parser import _fallback_outcome

    result = _fallback_outcome("好的知道了")
    assert result.outcome_tag == "no_response"
    assert result.met_signal is False


@pytest.mark.asyncio
async def test_parse_observe_outcome_success():
    """LLM returns valid JSON -> return OutcomeExtraction."""
    from sales_agent.services.sales_actions.parser import parse_observe_outcome

    mock_model = AsyncMock()
    mock_model.generate.return_value = (
        '{"outcome_tag": "achieved", "outcome_note": "约到明天下午", "met_signal": true, "confidence": 0.95}'
    )

    result = await parse_observe_outcome(
        reply="约到了，明天下午见面",
        success_criteria="客户确认见面时间",
        chat_model=mock_model,
    )
    assert result.outcome_tag == "achieved"
    assert result.met_signal is True
    assert result.confidence == 0.95
    assert result.parse_failed is False


@pytest.mark.asyncio
async def test_parse_observe_outcome_retry_then_fallback():
    """Both attempts fail -> fallback to keyword heuristic."""
    from sales_agent.services.sales_actions.parser import parse_observe_outcome

    mock_model = AsyncMock()
    mock_model.generate.side_effect = RuntimeError("LLM down")

    result = await parse_observe_outcome(
        reply="约到了",
        success_criteria="客户确认见面时间",
        chat_model=mock_model,
    )
    assert result.outcome_tag in OUTCOME_TAGS
    assert result.parse_failed is True
    # Should have been called twice
    assert mock_model.generate.call_count == 2


@pytest.mark.asyncio
async def test_parse_observe_outcome_invalid_tag_retry():
    """First attempt returns invalid tag -> retry, then fallback."""
    from sales_agent.services.sales_actions.parser import parse_observe_outcome

    mock_model = AsyncMock()
    mock_model.generate.return_value = '{"outcome_tag": "invalid_tag", "confidence": 0.9}'

    result = await parse_observe_outcome(
        reply="约到了",
        success_criteria="客户确认见面时间",
        chat_model=mock_model,
    )
    assert result.outcome_tag in OUTCOME_TAGS
    assert result.parse_failed is True


@pytest.mark.asyncio
async def test_parse_observe_outcome_with_fenced_json():
    """LLM returns code-fenced JSON -> parses correctly."""
    from sales_agent.services.sales_actions.parser import parse_observe_outcome

    mock_model = AsyncMock()
    mock_model.generate.return_value = (
        "```json\n{\"outcome_tag\": \"partial\", \"outcome_note\": \"回了消息\", \"met_signal\": false, \"confidence\": 0.8}\n```"
    )

    result = await parse_observe_outcome(
        reply="他回了消息",
        success_criteria="确认时间",
        chat_model=mock_model,
    )
    assert result.outcome_tag == "partial"
    assert result.parse_failed is False


def test_outcome_tags_immutable():
    with pytest.raises(AttributeError):
        OUTCOME_TAGS.add("new_tag")  # type: ignore[attr-defined]
