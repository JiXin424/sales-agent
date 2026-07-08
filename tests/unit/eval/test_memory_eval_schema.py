# tests/unit/eval/test_memory_eval_schema.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.memory_eval.schema import (
    ExpectedTurn,
    FinalExpected,
    MultiturnScenario,
    ScenarioTurn,
)


def _scenario_dict():
    return {
        "id": "memory-cross-session-001",
        "version": 1,
        "tags": ["long_term", "profile", "cross_session"],
        "initial_state": {},
        "turns": [
            {
                "input": "记住我负责华东区",
                "time_offset_seconds": 0,
                "restart_before": False,
                "expected": {
                    "turn_relation": "new",
                    "memory_operation": "remember",
                    "active_memory_keys": ["sales_region"],
                    "reply_contains": ["华东"],
                },
            }
        ],
        "final_expected": {"active_topic_count": 1, "cross_scope_leakage": False},
    }


def test_scenario_loads_from_spec_example():
    s = MultiturnScenario(**_scenario_dict())
    assert s.id == "memory-cross-session-001"
    assert s.version == 1
    assert s.turns[0].expected.memory_operation == "remember"
    assert s.final_expected.active_topic_count == 1


def test_unknown_turn_relation_rejected():
    data = _scenario_dict()
    data["turns"][0]["expected"]["turn_relation"] = "bogus"
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)


def test_first_turn_cannot_be_duplicate():
    data = _scenario_dict()
    data["turns"][0]["duplicate_previous_event"] = True
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)


def test_turn_count_bounds():
    data = _scenario_dict()
    data["turns"] = [{"input": str(i), "expected": {}} for i in range(7)]
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)
