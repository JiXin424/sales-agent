"""Immutable report document types shared across the reporting layer.

These are plain data objects — they carry no DB or rendering logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MetricDirection = Literal["higher", "lower"]
MetricAggregation = Literal["mean", "pass_rate", "p95", "rate"]
CaseClassification = Literal["improved", "regressed", "unchanged", "new", "error"]
ReportType = Literal["candidate", "final"]
Recommendation = Literal[
    "improved", "neutral", "regressed",
    "do_not_publish", "rollback_recommended",
]


@dataclass(frozen=True)
class MetricDefinition:
    """Definition of one metric inside a versioned effect formula."""

    name: str
    group: str
    aliases: tuple[str, ...] = ()
    aggregation: MetricAggregation = "mean"
    direction: MetricDirection = "higher"
    minimum: float = 0.0
    maximum: float = 1.0
    hard_gate: str | None = None  # gate label; None = no gate

    def normalize(self, value: float) -> float:
        """Normalize *value* into [0, 1] respecting direction."""
        span = self.maximum - self.minimum
        if span == 0:
            return 0.5
        clamped = max(self.minimum, min(self.maximum, value))
        ratio = (clamped - self.minimum) / span
        return 1.0 - ratio if self.direction == "lower" else ratio


@dataclass(frozen=True)
class EffectFormula:
    """Versioned composite effect formula.

    Each formula declares its own group weights and metric registry so newer
    versions can evolve independently.
    """

    version: str
    group_weights: dict[str, float]
    metrics: tuple[MetricDefinition, ...]

    def metrics_for_group(self, group: str) -> list[MetricDefinition]:
        return [m for m in self.metrics if m.group == group]


@dataclass
class GroupEffect:
    """Computed effect for one score group."""

    group_name: str
    weight: float
    score_before: float | None = None
    score_after: float | None = None
    delta: float | None = None
    coverage: int = 0
    total_metrics: int = 0
    metrics: list[MetricEffect] = field(default_factory=list)


@dataclass
class MetricEffect:
    """Computed before/after for a single metric."""

    metric_name: str
    direction: MetricDirection
    weight: float
    before_value: float | None = None
    after_value: float | None = None
    before_normalized: float | None = None
    after_normalized: float | None = None
    delta: float | None = None
    applicable: bool = True
    gate_result: str | None = None


@dataclass
class ReportDecision:
    """Top-level recommendation and hard-gate summary."""

    recommendation: Recommendation
    composite_before: float | None = None
    composite_after: float | None = None
    composite_delta: float | None = None
    hard_gates_failed: list[str] = field(default_factory=list)
    groups: list[GroupEffect] = field(default_factory=list)


@dataclass
class CaseEffect:
    """Per-eval-case before/after classification."""

    case_id: str
    classification: CaseClassification = "unchanged"
    cause: str | None = None
    before_pass: bool | None = None
    after_pass: bool | None = None
    score_delta: float | None = None
    rank_delta: int | None = None
    latency_delta_ms: float | None = None
    token_delta: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
