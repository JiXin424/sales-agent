from __future__ import annotations

from eval.memory_eval.gates import GATES, GateReport, check_gates
from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.report import EvaluationReport
from eval.memory_eval.versions import VersionBundle


def _vb():
    return VersionBundle("m", "p", "c", "d", "k", "ms", "g")


def test_passing_report_meets_all_gates():
    # Must satisfy EVERY gate in GATES, including all three fail-closed
    # safety gates (check_gates appends a failure for an unsatisfied
    # safety gate, so omitting them would wrongly flip gr.passed to False).
    r = EvaluationReport(versions=_vb())
    r.add_metric("deterministic_state_safety", MetricResult(
        name="turn_relation_accuracy", numerator=9, denominator=10, score=0.95, threshold=0.9))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="topic_leakage_rate", numerator=0, denominator=2, score=0.0, threshold=0.0))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="clarification_completion", numerator=9, denominator=10, score=0.9, threshold=0.9))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="restart_recovery", numerator=2, denominator=2, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="explicit_operation_accuracy", numerator=5, denominator=5, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="correction_supersede_accuracy", numerator=3, denominator=3, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="forget_effectiveness", numerator=2, denominator=2, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="prohibited_memory_write_count", numerator=0, denominator=0, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="cross_scope_leakage", numerator=0, denominator=1, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="assistant_output_to_user_memory", numerator=0, denominator=1, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="relevant_memory_precision", numerator=9, denominator=10, score=0.9, threshold=0.9))
    gr = check_gates(r)
    assert gr.passed is True
    assert gr.failed_gates == []


def test_leakage_gate_fails_closed():
    r = EvaluationReport(versions=_vb())
    r.add_metric("persistence_isolation", MetricResult(
        name="prohibited_memory_write_count", numerator=1, denominator=1, score=1.0, threshold=0.0))
    gr = check_gates(r)
    assert gr.passed is False
    assert any("prohibited_memory_write_count" in g for g in gr.failed_gates)
