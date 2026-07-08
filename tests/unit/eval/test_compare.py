from __future__ import annotations

from eval.memory_eval.runner import compare_reports


def _report(metrics, schema_version="0013"):
    return {
        "versions": {"memory_schema_version": schema_version},
        "groups": {"deterministic_state_safety": metrics},
    }


def test_incompatible_schema_excluded():
    base = _report([{"name": "turn_relation_accuracy", "score": 0.9}], "0013")
    cand = _report([{"name": "turn_relation_accuracy", "score": 0.95}], "0014")
    out = compare_reports(base, cand)
    assert out["schema_compatible"] is False
    assert out["labels"] == {}


def test_labels_regressed_fixed_new():
    base = _report([
        {"name": "turn_relation_accuracy", "score": 0.95, "error": None},
        {"name": "explicit_operation_accuracy", "score": 1.0, "error": None},
    ])
    cand = _report([
        {"name": "turn_relation_accuracy", "score": 0.90, "error": None},   # regressed
        {"name": "explicit_operation_accuracy", "score": 1.0, "error": None},  # unchanged
        {"name": "new_metric", "score": 0.9, "error": None},                # new
    ])
    out = compare_reports(base, cand)
    assert out["labels"]["turn_relation_accuracy"] == "regressed"
    assert out["labels"]["new_metric"] == "new"


def test_labels_evaluator_error_and_flaky():
    base = _report([{"name": "relevance", "score": 0.8, "error": None}])
    cand = _report([{"name": "relevance", "score": 0.0, "error": "judge timeout"}])
    out = compare_reports(base, cand)
    assert out["labels"]["relevance"] == "evaluator_error"
