from __future__ import annotations

from eval.memory_eval.metrics.recall_profile import evaluate_recall_profile
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _pair(expected_ids, observed_ids, profile_match=True):
    scenario = MultiturnScenario(
        id="r1",
        turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(
            selected_memory_ids=expected_ids,
            active_memory_keys=expected_ids,
        ))],
    )
    run = ScenarioRun(scenario_id="r1", observed=[ObservedTurn(
        turn_index=0,
        result={"profile_field_match": profile_match},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=observed_ids, selected_memory_ids=observed_ids,
        profile_version="v1" if profile_match else None,
    )], final_state={})
    return [(scenario, run)]


def test_relevant_memory_precision_recall():
    metrics, _ = evaluate_recall_profile(_pair(["m1", "m2"], ["m1", "m3"]))
    by_name = {m.name: m for m in metrics}
    # tp=1 (m1), fp=1 (m3), fn=1 (m2) → precision 0.5, recall 0.5
    assert round(by_name["relevant_memory_precision"].score, 4) == 0.5
    assert round(getattr(by_name["relevant_memory_precision"], "recall"), 4) == 0.5


def test_cross_scope_leakage_gate():
    # final_expected.cross_scope_leakage True → gate fails closed.
    scenario = MultiturnScenario(id="r2", turns=[ScenarioTurn(input="t0", expected=ExpectedTurn())])
    scenario.final_expected.cross_scope_leakage = True
    run = ScenarioRun(scenario_id="r2", observed=[ObservedTurn(
        turn_index=0, result={}, replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={})
    metrics, _ = evaluate_recall_profile([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["cross_scope_leakage"].passes is False
