"""Versioned effect formulas.

The effect index is a normalized 0–100 composite that renormalises across
applicable metric groups.  Each formula declares its own group weights and
metric registry so later versions can evolve independently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .types import (
    CaseClassification,
    CaseEffect,
    EffectFormula,
    GroupEffect,
    MetricDefinition,
    MetricEffect,
    ReportDecision,
    ReportType,
)


# ── Effect v1 ────────────────────────────────────────────────────────────────

EFFECT_V1 = EffectFormula(
    version="effect-v1",
    group_weights={
        "answer_quality": 0.30,
        "retrieval": 0.30,
        "routing": 0.15,
        "safety_refusal": 0.15,
        "efficiency": 0.10,
    },
    metrics=(
        # --- answer_quality ---
        MetricDefinition("answer_correctness", "answer_quality",
                         aliases=("correctness", "answer_score"),
                         aggregation="pass_rate", direction="higher",
                         minimum=0.0, maximum=1.0),
        MetricDefinition("factual_accuracy", "answer_quality",
                         aliases=("factual_score",),
                         aggregation="pass_rate", direction="higher",
                         minimum=0.0, maximum=1.0,
                         hard_gate="critical_fact_error"),
        MetricDefinition("completeness", "answer_quality",
                         aliases=("answer_completeness",),
                         aggregation="mean", direction="higher",
                         minimum=0.0, maximum=1.0),

        # --- retrieval ---
        MetricDefinition("contextual_precision", "retrieval",
                         aliases=("precision", "context_precision"),
                         aggregation="mean", direction="higher",
                         minimum=0.0, maximum=1.0),
        MetricDefinition("contextual_recall", "retrieval",
                         aliases=("recall", "context_recall"),
                         aggregation="mean", direction="higher",
                         minimum=0.0, maximum=1.0),
        MetricDefinition("contextual_relevancy", "retrieval",
                         aliases=("relevancy",),
                         aggregation="mean", direction="higher",
                         minimum=0.0, maximum=1.0),

        # --- routing ---
        MetricDefinition("route_accuracy", "routing",
                         aliases=("router_accuracy",),
                         aggregation="pass_rate", direction="higher",
                         minimum=0.0, maximum=1.0),

        # --- safety_refusal ---
        MetricDefinition("safety_pass_rate", "safety_refusal",
                         aliases=("safety",),
                         aggregation="pass_rate", direction="higher",
                         minimum=0.0, maximum=1.0,
                         hard_gate="unsafe_response"),
        MetricDefinition("refusal_correctness", "safety_refusal",
                         aliases=("refusal_accuracy",),
                         aggregation="pass_rate", direction="higher",
                         minimum=0.0, maximum=1.0),
        MetricDefinition("fabrication_on_unanswerable", "safety_refusal",
                         aliases=("hallucination_on_unanswerable", "unanswerable_fabrication"),
                         aggregation="rate", direction="lower",
                         minimum=0.0, maximum=1.0,
                         hard_gate="increased_fabrication"),

        # --- efficiency ---
        MetricDefinition("p95_latency_ms", "efficiency",
                         aliases=("latency_p95",),
                         aggregation="p95", direction="lower",
                         minimum=0.0, maximum=10000.0),
        MetricDefinition("token_usage", "efficiency",
                         aliases=("total_tokens",),
                         aggregation="mean", direction="lower",
                         minimum=0.0, maximum=100000.0),
    ),
)

# Registry of all known formulas
_FORMULAS: dict[str, EffectFormula] = {EFFECT_V1.version: EFFECT_V1}


def get_formula(version: str) -> EffectFormula:
    """Return the registered formula for *version*."""
    formula = _FORMULAS.get(version)
    if formula is None:
        raise ValueError(f"Unknown formula version: {version}")
    return formula


# ── Matching helpers ─────────────────────────────────────────────────────────

def _find_metric(formula: EffectFormula, metric_name: str) -> MetricDefinition | None:
    """Match a DeepEval metric name to a formula metric."""
    for m in formula.metrics:
        if m.name == metric_name or metric_name in m.aliases:
            return m
    return None


# ── Normalised composite ─────────────────────────────────────────────────────

def compute_effect(
    formula: EffectFormula,
    report_type: ReportType,
    baseline_metrics: dict[str, float],
    candidate_metrics: dict[str, float],
    hard_gates: dict[str, bool] | None = None,
) -> ReportDecision:
    """Compute a versioned effect report from raw before/after metric values.

    Parameters
    ----------
    formula:
        The formula version to apply.
    report_type:
        ``candidate`` or ``final`` — affects hard-gate recommendation labels.
    baseline_metrics:
        Raw metric-name → value for the baseline evaluation.
    candidate_metrics:
        Raw metric-name → value for the candidate/post-publish evaluation.
    hard_gates:
        Gate label → ``True`` if the gate passed (no violation).

    Returns
    -------
    ReportDecision
        Normalised composite score, per-group effects, gates, and recommendation.
    """
    hard_gates = dict(hard_gates) if hard_gates else {}

    groups: list[GroupEffect] = []
    for group_name, group_weight in formula.group_weights.items():
        group_metrics = formula.metrics_for_group(group_name)
        metric_effects: list[MetricEffect] = []
        before_scores: list[float] = []
        after_scores: list[float] = []
        coverage = 0

        for metric_def in group_metrics:
            before_raw = baseline_metrics.get(metric_def.name)
            after_raw = candidate_metrics.get(metric_def.name)

            applicable = before_raw is not None and after_raw is not None
            before_norm = metric_def.normalize(before_raw) if before_raw is not None else None
            after_norm = metric_def.normalize(after_raw) if after_raw is not None else None
            delta = (after_norm - before_norm) if (before_norm is not None and after_norm is not None) else None

            gate_result: str | None = None
            if metric_def.hard_gate and metric_def.hard_gate in hard_gates:
                gate_result = "pass" if hard_gates[metric_def.hard_gate] else "fail"

            if applicable:
                before_scores.append(before_norm)  # type: ignore[arg-type]
                after_scores.append(after_norm)  # type: ignore[arg-type]
                coverage += 1

            metric_effects.append(MetricEffect(
                metric_name=metric_def.name,
                direction=metric_def.direction,
                weight=1.0 / max(len(group_metrics), 1),
                before_value=before_raw,
                after_value=after_raw,
                before_normalized=before_norm,
                after_normalized=after_norm,
                delta=delta,
                applicable=applicable,
                gate_result=gate_result,
            ))

        # Group score: mean of applicable normalized values
        group_before = _safe_mean(before_scores)
        group_after = _safe_mean(after_scores)
        group_delta = (group_after - group_before) if (group_before is not None and group_after is not None) else None

        groups.append(GroupEffect(
            group_name=group_name,
            weight=group_weight,
            score_before=group_before,
            score_after=group_after,
            delta=group_delta,
            coverage=coverage,
            total_metrics=len(group_metrics),
            metrics=metric_effects,
        ))

    # Composite: weighted mean across groups that have coverage > 0
    applicable_groups = [g for g in groups if g.coverage > 0]
    if applicable_groups:
        total_weight = sum(g.weight for g in applicable_groups)
        if total_weight > 0:
            composite_before = sum(
                (g.score_before or 0.0) * g.weight for g in applicable_groups
            ) / total_weight
            composite_after = sum(
                (g.score_after or 0.0) * g.weight for g in applicable_groups
            ) / total_weight
        else:
            composite_before = composite_after = None
    else:
        composite_before = composite_after = None

    composite_delta = (
        (composite_after - composite_before)
        if (composite_before is not None and composite_after is not None)
        else None
    )

    # Hard gates
    hard_gate_failures: list[str] = []
    for metric_def in formula.metrics:
        if metric_def.hard_gate:
            passed = hard_gates.get(metric_def.hard_gate, True)
            if not passed:
                hard_gate_failures.append(metric_def.hard_gate)

    # Also check for tenant_leakage gate (meta-gate not tied to a single metric)
    if "tenant_leakage" in hard_gates and not hard_gates["tenant_leakage"]:
        if "tenant_leakage" not in hard_gate_failures:
            hard_gate_failures.append("tenant_leakage")

    # Recommendation
    recommendation = _determine_recommendation(report_type, composite_delta, hard_gate_failures)

    return ReportDecision(
        recommendation=recommendation,
        composite_before=_pct(composite_before),
        composite_after=_pct(composite_after),
        composite_delta=_pct(composite_delta),
        hard_gates_failed=hard_gate_failures,
        groups=groups,
    )


def _determine_recommendation(
    report_type: ReportType,
    delta: float | None,
    hard_gate_failures: list[str],
) -> str:
    """Precedence order: hard-gate failure → delta-based → neutral."""
    if hard_gate_failures:
        return "rollback_recommended" if report_type == "final" else "do_not_publish"
    if delta is None:
        return "neutral"
    if delta > 0.02:
        return "improved"
    if delta < -0.02:
        return "regressed"
    return "neutral"


# ── Case classification ──────────────────────────────────────────────────────

def classify_case(
    *,
    case_id: str,
    before_pass: bool | None,
    after_pass: bool | None,
    before_score: float | None = None,
    after_score: float | None = None,
    is_new_case: bool = False,
    had_error: bool = False,
    rank_delta: int | None = None,
    latency_delta_ms: float | None = None,
    token_delta: int | None = None,
) -> CaseEffect:
    """Classify a single evaluation case result.

    Rules (first match wins):
    1. Error → ``error``
    2. No baseline match → ``new``
    3. Pass transition: fail→pass → ``improved``, pass→fail → ``regressed``
    4. Score tolerance (0.02) → ``improved`` / ``regressed`` / ``unchanged``
    """
    classification: CaseClassification
    cause: str | None = None
    score_delta = None

    if had_error:
        classification = "error"
        cause = "evaluation_error"
    elif is_new_case:
        classification = "new"
        cause = "no_baseline_match"
    elif before_pass is not None and after_pass is not None:
        if not before_pass and after_pass:
            classification = "improved"
            cause = "pass_transition"
        elif before_pass and not after_pass:
            classification = "regressed"
            cause = "pass_transition"
        elif before_score is not None and after_score is not None:
            score_delta = after_score - before_score
            if score_delta > 0.02:
                classification = "improved"
                cause = "score_improvement"
            elif score_delta < -0.02:
                classification = "regressed"
                cause = "score_regression"
            else:
                classification = "unchanged"
                cause = "within_tolerance"
        else:
            classification = "unchanged"
            cause = "no_delta"
    else:
        classification = "unchanged"
        cause = "insufficient_data"

    return CaseEffect(
        case_id=case_id,
        classification=classification,
        cause=cause,
        before_pass=before_pass,
        after_pass=after_pass,
        score_delta=score_delta,
        rank_delta=rank_delta,
        latency_delta_ms=latency_delta_ms,
        token_delta=token_delta,
    )


# ── Data snapshot ────────────────────────────────────────────────────────────

def compute_snapshot_hash(inputs: dict[str, Any]) -> str:
    """Return SHA-256 hex digest of the canonical sorted JSON of *inputs*."""
    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _pct(value: float | None) -> float | None:
    """Scale [0, 1] → [0, 100] and round to 2 decimal places."""
    if value is None:
        return None
    return round(value * 100, 2)
