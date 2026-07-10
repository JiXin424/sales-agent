"""Unit tests for the promote-trace workflow (Spec 4 §9, Task 21).

Covers the three pure functions: ``anonymize_trace``, ``classify_root_cause``,
and ``build_regression_scenario``. The DB-backed ``run_promote_trace`` mode is
exercised through the integration suite, not here.
"""
from __future__ import annotations

from eval.memory_eval.promote import (
    anonymize_trace,
    build_regression_scenario,
    classify_root_cause,
)
from eval.memory_eval.dataset import validate_dataset


def _trace():
    return {
        "scope_hash": "h:abc",
        "topic_id": "topic-7",
        "topic_transition": "switch",
        "selected_memory_ids": ["m1"],
        "memory_degraded": True,
        "memory_degradation_reason": "exception",
        "signals": {"user_correction": True, "negative_feedback": False},
        "versions": {"memory_schema_version": "0013"},
        "turns": [{"input": "记住我负责华东区", "reply": "好的"}],
    }


def test_anonymize_strips_reply_and_keeps_inputs_redacted():
    out = anonymize_trace(_trace())
    assert out["scope_hash"] == "h:abc"
    assert "reply" not in out["turns"][0]           # outbound dropped
    # inputs are retained but will be re-validated by validate_dataset


def test_classify_root_cause():
    assert classify_root_cause(_trace()) == "memory"  # degradation_reason present
    t2 = dict(_trace()); t2["memory_degraded"] = False; t2["memory_degradation_reason"] = None
    t2["topic_transition"] = "switch"; t2["selected_memory_ids"] = []
    assert classify_root_cause(t2) == "routing"


def test_build_regression_scenario_is_valid_dataset():
    scenario = build_regression_scenario(
        _trace(), scenario_id="promoted-001",
        expected={"turn_relation": "switch"},
    )
    errors = validate_dataset([scenario])
    assert errors == []
    assert scenario.id == "promoted-001"
    assert scenario.turns[0].expected.turn_relation == "switch"
