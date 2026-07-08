from __future__ import annotations

from eval.memory_eval.metrics.types import (
    ConfusionMatrix,
    MetricResult,
    confusion,
    prf,
)


def test_prf_computes_precision_recall_f1():
    # 3 predicted positives, 2 correct; 2 actual positives.
    r = prf(tp=2, fp=1, fn=0)
    assert r.numerator == 2 and r.denominator == 3
    assert round(r.score, 4) == round(2 / 3, 4)


def test_prf_no_predictions_is_na_not_zero():
    r = prf(tp=0, fp=0, fn=2)
    assert r.applicable is False
    assert r.score == 0.0  # reported but marked not applicable


def test_metric_result_records_threshold_and_error():
    r = MetricResult(name="x", numerator=9, denominator=10, score=0.9, threshold=0.9)
    assert r.threshold == 0.9
    assert r.error is None
    assert r.sample_ids == []


def test_confusion_matrix_counts_matches():
    cm = confusion(
        expected=["new", "new", "continue"],
        observed=["new", "continue", "continue"],
        labels=["new", "continue"],
    )
    assert isinstance(cm, ConfusionMatrix)
    assert cm.matrix["new"]["new"] == 1
    assert cm.matrix["new"]["continue"] == 1
    assert cm.matrix["continue"]["continue"] == 1
