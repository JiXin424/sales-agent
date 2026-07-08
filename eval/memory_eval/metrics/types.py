"""Shared metric containers (Spec 4 §5, §7).

Every metric records applicability, numerator, denominator, score, threshold,
error, and sample IDs (§7). Missing inputs are marked not-applicable, never
perfect (§2.6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MetricResult:
    name: str
    numerator: int = 0
    denominator: int = 0
    score: float = 0.0
    threshold: Optional[float] = None
    applicable: bool = True
    error: Optional[str] = None
    sample_ids: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        if self.error is not None or not self.applicable:
            return True  # errors/N-A never flip a product gate (§10)
        if self.threshold is None:
            return True
        return self.score >= self.threshold


@dataclass
class ConfusionMatrix:
    labels: list[str]
    matrix: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {row: dict(cols) for row, cols in self.matrix.items()}


def prf(*, tp: int, fp: int, fn: int, threshold: Optional[float] = None) -> MetricResult:
    """Precision/Recall/F1 rolled into one MetricResult (score = precision)."""
    predicted = tp + fp
    applicable = predicted > 0
    precision = tp / predicted if predicted else 0.0
    res = MetricResult(
        name="prf",
        numerator=tp,
        denominator=predicted,
        score=precision,
        threshold=threshold,
        applicable=applicable,
    )
    # Stash recall/f1 as extra attrs for callers that want them.
    res.recall = tp / (tp + fn) if (tp + fn) else 0.0  # type: ignore[attr-defined]
    f1 = (2 * precision * res.recall / (precision + res.recall)) if (precision + res.recall) else 0.0
    res.f1 = f1  # type: ignore[attr-defined]
    return res


def confusion(*, expected: list[str], observed: list[str], labels: list[str]) -> ConfusionMatrix:
    """Build a labels×labels confusion matrix from paired sequences."""
    matrix = {row: {col: 0 for col in labels} for row in labels}
    for exp, obs in zip(expected, observed):
        if exp in matrix and obs in matrix[exp]:
            matrix[exp][obs] += 1
    return ConfusionMatrix(labels=labels, matrix=matrix)


__all__ = ["ConfusionMatrix", "MetricResult", "confusion", "prf"]
