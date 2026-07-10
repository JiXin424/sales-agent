"""Conversation & trajectory metrics (Spec 4 §5.4)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun


def percentile(values: list[float], p: float) -> float:
    """Nearest-index percentile (C=1 linear interpolation at boundaries)."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = round((len(s) - 1) * p / 100.0)
    return float(s[max(0, min(idx, len(s) - 1))])


def evaluate_conversation(pairs):
    metrics: list[MetricResult] = []

    node_match, node_total = 0, 0
    outcome_ok, outcome_total = 0, 0
    latencies: list[float] = []
    token_totals: list[int] = []

    for scenario, run in pairs:
        if scenario.final_expected is not None:
            outcome_total += 1
            if run.final_state.get("outcome_ok"):
                outcome_ok += 1
        for i, turn in enumerate(scenario.turns):
            if i >= len(run.observed):
                continue
            obs = run.observed[i]
            if turn.expected.trace_nodes:
                observed_nodes = set(obs.result.get("trace_nodes") or [])
                node_total += len(turn.expected.trace_nodes)
                node_match += sum(1 for n in turn.expected.trace_nodes if n in observed_nodes)
            if "latency_ms" in obs.result:
                latencies.append(float(obs.result["latency_ms"]))
            if "total_tokens" in obs.result:
                token_totals.append(int(obs.result["total_tokens"]))

    if node_total:
        metrics.append(MetricResult(
            name="node_subset_recall", numerator=node_match, denominator=node_total,
            score=node_match / node_total, threshold=1.0,
        ))
    if outcome_total:
        metrics.append(MetricResult(
            name="final_outcome", numerator=outcome_ok, denominator=outcome_total,
            score=outcome_ok / outcome_total, threshold=0.95,
        ))
    if latencies:
        metrics.append(MetricResult(
            name="p50_latency_ms", numerator=len(latencies), denominator=len(latencies),
            score=percentile(latencies, 50),
        ))
        metrics.append(MetricResult(
            name="p95_latency_ms", numerator=len(latencies), denominator=len(latencies),
            score=percentile(latencies, 95),
        ))
    if token_totals:
        metrics.append(MetricResult(
            name="total_tokens", numerator=sum(token_totals), denominator=len(token_totals),
            score=float(sum(token_totals)),
        ))
    return metrics, {}
