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


def test_at_most_safety_metric_increase_is_regression():
    # For an at_most safety metric (cross_scope_leakage), a score INCREASE is a
    # regression, not an improvement (§7). Direction-aware labelling relies on
    # the serialized ``pass_if`` field.
    base = _report([{"name": "cross_scope_leakage", "score": 0.0,
                     "pass_if": "at_most", "error": None}])
    cand = _report([{"name": "cross_scope_leakage", "score": 0.5,
                     "pass_if": "at_most", "error": None}])
    out = compare_reports(base, cand)
    assert out["labels"]["cross_scope_leakage"] == "regressed"


def test_at_most_safety_metric_decrease_is_improvement():
    # Symmetric: a DECREASE in an at_most safety metric is an improvement.
    base = _report([{"name": "prohibited_memory_write_count", "score": 2.0,
                     "pass_if": "at_most", "error": None}])
    cand = _report([{"name": "prohibited_memory_write_count", "score": 0.0,
                     "pass_if": "at_most", "error": None}])
    out = compare_reports(base, cand)
    assert out["labels"]["prohibited_memory_write_count"] == "improved"


def test_at_least_quality_metric_still_direction_normal():
    # A normal at_least quality metric: increase = improved (unchanged behavior).
    base = _report([{"name": "turn_relation_accuracy", "score": 0.9,
                     "pass_if": "at_least", "error": None}])
    cand = _report([{"name": "turn_relation_accuracy", "score": 0.95,
                     "pass_if": "at_least", "error": None}])
    out = compare_reports(base, cand)
    assert out["labels"]["turn_relation_accuracy"] == "improved"
