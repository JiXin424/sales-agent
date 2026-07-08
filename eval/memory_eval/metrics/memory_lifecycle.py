"""Memory write & lifecycle metrics (Spec 4 §5.2)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

_EXPLICIT_OPS = {"remember", "correct", "forget"}


def _turn_pairs(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]],
):
    for scenario, run in pairs:
        for i, turn in enumerate(scenario.turns):
            if i < len(run.observed):
                yield turn, run.observed[i]


def evaluate_memory_lifecycle(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]],
) -> tuple[list[MetricResult], dict]:
    metrics: list[MetricResult] = []

    # Explicit operation accuracy (§6: 100% on deterministic cases)
    exp_correct, exp_total = 0, 0
    # Inferred activation precision/recall
    inf_tp, inf_fp, inf_fn = 0, 0, 0
    # Correction/supersede accuracy
    corr_correct, corr_total = 0, 0
    # Forget effectiveness (active memory count == 0 after forget)
    forget_ok, forget_total = 0, 0
    # Prohibited/sensitive persisted
    sensitive_total = 0
    # Evidence provenance completeness
    provenance_total, provenance_expected = 0, 0

    for turn, obs in _turn_pairs(pairs):
        exp_op = turn.expected.memory_operation
        obs_op = obs.result.get("memory_operation")
        if exp_op in _EXPLICIT_OPS:
            exp_total += 1
            if exp_op == obs_op:
                exp_correct += 1
        if exp_op == "correct":
            corr_total += 1
            if obs_op == "correct":
                corr_correct += 1
        if exp_op == "forget":
            forget_total += 1
            if obs_op == "forget" and not obs.active_memory_keys:
                forget_ok += 1
        if turn.expected.sensitive_persisted is not None:
            sensitive_total += int(turn.expected.sensitive_persisted or 0) + max(
                0, len([k for k in obs.active_memory_keys if k in {"password", "secret", "token", "密码"}])
            )
        if turn.expected.active_memory_keys:
            provenance_expected += len(turn.expected.active_memory_keys)
            provenance_total += sum(
                1 for k in turn.expected.active_memory_keys if k in obs.active_memory_keys
            )

    if exp_total:
        metrics.append(MetricResult(
            name="explicit_operation_accuracy", numerator=exp_correct, denominator=exp_total,
            score=exp_correct / exp_total, threshold=1.0,
        ))
    if corr_total:
        metrics.append(MetricResult(
            name="correction_supersede_accuracy", numerator=corr_correct, denominator=corr_total,
            score=corr_correct / corr_total, threshold=1.0,
        ))
    if forget_total:
        metrics.append(MetricResult(
            name="forget_effectiveness", numerator=forget_ok, denominator=forget_total,
            score=forget_ok / forget_total, threshold=1.0,
        ))
    metrics.append(MetricResult(
        name="prohibited_memory_write_count", numerator=sensitive_total, denominator=max(sensitive_total, 1),
        score=-float(sensitive_total), threshold=0.0,  # fail closed: any violation → score < 0
    ))
    if provenance_expected:
        metrics.append(MetricResult(
            name="evidence_provenance_completeness", numerator=provenance_total,
            denominator=provenance_expected, score=provenance_total / provenance_expected, threshold=1.0,
        ))
    return metrics, {}
