from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sales_agent.services.sales_actions.contracts import SalesActionExtraction
from sales_agent.services.sales_actions.time_parser import validate_action_extraction


NOW = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def extraction(**overrides):
    data = {
        "intent": "create_action",
        "explicit_create": True,
        "title": "给张总回电话",
        "customer_name": "张总",
        "action_type": "call_back",
        "time_text": "半小时后",
        "scheduled_at": "2026-07-10T15:30:00+08:00",
        "timezone": "Asia/Shanghai",
        "confidence": 0.92,
        "missing_fields": [],
        "needs_clarification": False,
        "clarification_question": None,
    }
    data.update(overrides)
    return SalesActionExtraction(**data)


def test_clear_future_time_is_accepted():
    decision = validate_action_extraction(extraction(), now=NOW)
    assert decision.action == "create"
    assert decision.scheduled_at.isoformat() == "2026-07-10T15:30:00+08:00"


def test_low_confidence_requires_clarification():
    decision = validate_action_extraction(extraction(confidence=0.5), now=NOW)
    assert decision.action == "clarify"


def test_past_time_requires_clarification():
    decision = validate_action_extraction(
        extraction(scheduled_at="2026-07-10T14:00:00+08:00"),
        now=NOW,
    )
    assert decision.action == "clarify"
    assert "过去" in decision.response_text


def test_missing_title_requires_clarification():
    decision = validate_action_extraction(extraction(title="", missing_fields=["title"]), now=NOW)
    assert decision.action == "clarify"


def test_too_fuzzy_time_requires_clarification():
    decision = validate_action_extraction(
        extraction(time_text="这两天", needs_clarification=True, clarification_question="你想具体哪天提醒？"),
        now=NOW,
    )
    assert decision.action == "clarify"
    assert "哪天" in decision.response_text


def test_suggest_action_with_title_but_no_time_is_suggested():
    """A titled suggestion with no concrete time surfaces for confirmation,
    not forced into clarify (spec: Agent-inferred action items require user
    confirmation; the suggest example in the prompt has scheduled_at=null)."""
    decision = validate_action_extraction(
        extraction(
            intent="suggest_action",
            explicit_create=False,
            title="给李总发方案",
            time_text="尽快",
            scheduled_at=None,
            missing_fields=["scheduled_at"],
        ),
        now=NOW,
    )
    assert decision.action == "suggest"
    assert decision.title == "给李总发方案"
    assert decision.scheduled_at is None
