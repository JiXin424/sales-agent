from __future__ import annotations

from eval.memory_eval.metrics.conversation import evaluate_conversation, percentile


def test_percentile_basic():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


def test_node_subset_metric():
    from eval.memory_eval.schema import (
        ExpectedTurn, MultiturnScenario, ObservedTurn, ScenarioRun, ScenarioTurn,
    )
    scenario = MultiturnScenario(id="c1", turns=[ScenarioTurn(
        input="t0", expected=ExpectedTurn(trace_nodes=["chat", "memory_command"]),
    )])
    run = ScenarioRun(scenario_id="c1", observed=[ObservedTurn(
        turn_index=0,
        result={"trace_nodes": ["normalize_turn", "chat", "memory_command"], "latency_ms": 120.0},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={"outcome_ok": True})
    metrics, _ = evaluate_conversation([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["node_subset_recall"].score == 1.0
    assert by_name["p50_latency_ms"].numerator == 1
