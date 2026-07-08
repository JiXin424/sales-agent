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
    # Verify recall and f1 dynamic attributes
    assert r.recall == 1.0  # 2 / (2+0)
    assert round(r.f1, 4) == round(2 * (2 / 3) * 1.0 / ((2 / 3) + 1.0), 4)  # ≈0.8


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


def test_confusion_rejects_mismatched_lengths():
    import pytest

    with pytest.raises(ValueError, match="same length"):
        confusion(
            expected=["new", "new"],
            observed=["new"],
            labels=["new", "continue"],
        )


def test_passes_property_branches():
    # error → never flips gate
    assert MetricResult(name="x", error="boom").passes is True
    # N-A → never flips gate
    assert MetricResult(
        name="x", applicable=False, score=0.0, threshold=0.9
    ).passes is True
    # no threshold → passes
    assert MetricResult(name="x", threshold=None).passes is True
    # below threshold → fails
    assert MetricResult(
        name="x", score=0.8, threshold=0.9
    ).passes is False
    # at/above → passes
    assert MetricResult(
        name="x", score=0.95, threshold=0.9
    ).passes is True
