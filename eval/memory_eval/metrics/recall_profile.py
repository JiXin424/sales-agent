"""Recall & profile metrics (Spec 4 §5.3)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import MetricResult, prf
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun


def evaluate_recall_profile(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]],
) -> tuple[list[MetricResult], dict]:
    metrics: list[MetricResult] = []

    exp_set_union, obs_set_union = set(), set()
    profile_match_total, profile_expected = 0, 0
    unnecessary_injection = 0
    cross_leak = 0

    for scenario, run in pairs:
        if scenario.final_expected.cross_scope_leakage:
            cross_leak += 1
        for i, turn in enumerate(scenario.turns):
            if i >= len(run.observed):
                continue
            obs = run.observed[i]
            expected_ids = set(turn.expected.selected_memory_ids)
            observed_ids = set(obs.selected_memory_ids)
            if expected_ids:
                exp_set_union |= expected_ids
                obs_set_union |= observed_ids
                # unnecessary injection: observed ids not in expected
                unnecessary_injection += len(observed_ids - expected_ids)
            if turn.expected.active_memory_keys:
                profile_expected += 1
                if obs.result.get("profile_field_match"):
                    profile_match_total += 1

    if exp_set_union or obs_set_union:
        tp = len(exp_set_union & obs_set_union)
        fp = len(obs_set_union - exp_set_union)
        fn = len(exp_set_union - obs_set_union)
        m = prf(tp=tp, fp=fp, fn=fn, threshold=0.90)
        m.name = "relevant_memory_precision"
        metrics.append(m)

    if profile_expected:
        metrics.append(MetricResult(
            name="profile_field_accuracy", numerator=profile_match_total, denominator=profile_expected,
            score=profile_match_total / profile_expected, threshold=0.90,
        ))

    total_observed = len(obs_set_union)
    if total_observed:
        metrics.append(MetricResult(
            name="unnecessary_memory_injection_rate", numerator=unnecessary_injection,
            denominator=total_observed, score=unnecessary_injection / total_observed, threshold=0.10,
        ))

    # Cross-scope leakage: fail closed (§6).
    metrics.append(MetricResult(
        name="cross_scope_leakage", numerator=cross_leak, denominator=cross_leak or 1,
        score=float(cross_leak), threshold=0.0, pass_if="at_most",
    ))
    return metrics, {}
