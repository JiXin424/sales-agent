"""Turn & Topic metrics (Spec 4 §5.1)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import ConfusionMatrix, MetricResult, confusion
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

_RELATION_LABELS = ["continue", "revise", "switch", "new", "ambiguous"]


def _turn_pairs(pairs):
    for scenario, run in pairs:
        for i, turn in enumerate(scenario.turns):
            obs = run.observed[i] if i < len(run.observed) else None
            if obs is None:
                continue
            yield scenario, run, turn, obs


def evaluate_turn_topic(pairs):
    metrics: list[MetricResult] = []
    cms: dict[str, ConfusionMatrix] = {}

    exp_rels: list[str] = []
    obs_rels: list[str] = []
    q_match, q_total = 0, 0
    leak_n, leak_d = 0, 0

    for _, _, turn, obs in _turn_pairs(pairs):
        exp_rel = turn.expected.turn_relation
        obs_rel = obs.result.get("turn_relation")
        if exp_rel is not None and obs_rel is not None:
            exp_rels.append(exp_rel)
            obs_rels.append(obs_rel)

        # standalone-query retention recall: expected substrings present in observed query
        if turn.expected.standalone_query_contains:
            q_total += len(turn.expected.standalone_query_contains)
            observed_query = obs.result.get("standalone_query") or ""
            q_match += sum(1 for s in turn.expected.standalone_query_contains if s in observed_query)

        # Topic leakage: a new/switch turn that still carries retained entities.
        if exp_rel in ("new", "switch"):
            leak_d += 1
            retained = turn.expected.retained_entities
            observed_retained = obs.result.get("retained_entities") or []
            if retained or observed_retained:
                leak_n += 1

    # Overall accuracy
    if exp_rels:
        correct = sum(1 for e, o in zip(exp_rels, obs_rels) if e == o)
        metrics.append(MetricResult(
            name="turn_relation_accuracy",
            numerator=correct, denominator=len(exp_rels),
            score=correct / len(exp_rels), threshold=0.90,
        ))
        cms["turn_relation"] = confusion(expected=exp_rels, observed=obs_rels, labels=_RELATION_LABELS)

    # Standalone-query retention recall
    if q_total:
        metrics.append(MetricResult(
            name="standalone_query_retention_recall",
            numerator=q_match, denominator=q_total,
            score=q_match / q_total, threshold=0.90,
        ))

    # Topic leakage rate
    if leak_d:
        metrics.append(MetricResult(
            name="topic_leakage_rate",
            numerator=leak_n, denominator=leak_d,
            score=leak_n / leak_d, threshold=0.0,  # gate: zero critical leakage
        ))

    # --- Extension: clarification_completion & restart_recovery ---

    # Clarification completion: a pending clarification that is resolved on a later turn.
    clar_resolved, clar_total = 0, 0
    # Restart recovery: scenarios tagged "restart" whose post-restart turn still advances flow.
    restart_ok, restart_total = 0, 0
    for scenario, run in pairs:
        if "restart" in scenario.tags:
            restart_total += 1
            # Recovered if the post-restart observed turn has a non-duplicate response_kind.
            post = [o for o in run.observed if o.turn_index > 0]
            if post and not post[-1].duplicate and post[-1].result.get("response_kind") != "duplicate":
                restart_ok += 1
        had_pending = any(
            t.expected.topic_transition == "restored"
            or "clarification" in (t.expected.reply_contains or [])
            for t in scenario.turns[:-1]
        )
        if had_pending:
            clar_total += 1
            last = run.observed[-1] if run.observed else None
            if (last
                    and last.result.get("turn_relation") in ("continue", "revise")
                    and last.result.get("response_kind") != "duplicate"):
                clar_resolved += 1
    if clar_total:
        metrics.append(MetricResult(
            name="clarification_completion",
            numerator=clar_resolved, denominator=clar_total,
            score=clar_resolved / clar_total, threshold=0.90,
        ))
    if restart_total:
        metrics.append(MetricResult(
            name="restart_recovery",
            numerator=restart_ok, denominator=restart_total,
            score=restart_ok / restart_total, threshold=1.0,
        ))

    return metrics, cms
