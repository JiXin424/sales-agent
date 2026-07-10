"""Tests for turn/topic metrics (Spec 4 §5.1)."""
from __future__ import annotations

from eval.memory_eval.metrics.turn_topic import evaluate_turn_topic
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _pair(exp_rels, obs_rels, obs_query="华东区"):
    scenario = MultiturnScenario(
        id="s1",
        turns=[
            ScenarioTurn(input=f"t{i}", expected=ExpectedTurn(turn_relation=r, standalone_query_contains=["华东"]))
            for i, r in enumerate(exp_rels)
        ],
    )
    run = ScenarioRun(
        scenario_id="s1",
        observed=[
            ObservedTurn(
                turn_index=i,
                result={"turn_relation": r, "standalone_query": obs_query},
                replies=[],
                active_topic_ids=[],
                closed_topic_ids=[],
                active_memory_keys=[],
                selected_memory_ids=[],
            )
            for i, r in enumerate(obs_rels)
        ],
        final_state={},
    )
    return (scenario, run)


def test_turn_relation_accuracy_and_confusion():
    metrics, cms = evaluate_turn_topic([_pair(["new", "continue"], ["new", "new"])])
    by_name = {m.name: m for m in metrics}
    assert by_name["turn_relation_accuracy"].score == 0.5
    assert "turn_relation" in cms
    assert cms["turn_relation"].matrix["continue"]["new"] == 1


def test_standalone_query_retention_recall():
    metrics, _ = evaluate_turn_topic([_pair(["new"], ["new"], obs_query="别的")])
    by_name = {m.name: m for m in metrics}
    # expected substring "华东" absent in "别的" -> recall 0
    assert by_name["standalone_query_retention_recall"].score == 0.0


def test_topic_leakage_rate_for_new_switch():
    # A "new" turn that still carries retained entities leaks.
    scenario = MultiturnScenario(
        id="s2",
        turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(turn_relation="new", retained_entities=["stale"]))],
    )
    run = ScenarioRun(
        scenario_id="s2",
        observed=[ObservedTurn(
            turn_index=0,
            result={"turn_relation": "new", "retained_entities": ["stale"]},
            replies=[], active_topic_ids=[], closed_topic_ids=[],
            active_memory_keys=[], selected_memory_ids=[],
        )],
        final_state={},
    )
    metrics, _ = evaluate_turn_topic([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["topic_leakage_rate"].numerator == 1


# --- Extension tests ---

def test_restart_recovery_score_1():
    """A 'restart'-tagged scenario whose post-restart observed turn is non-duplicate -> score 1.0."""
    scenario = MultiturnScenario(
        id="s3",
        tags=["restart"],
        turns=[
            ScenarioTurn(input="t0", expected=ExpectedTurn(turn_relation="new")),
            ScenarioTurn(input="t1", expected=ExpectedTurn(turn_relation="new")),
        ],
    )
    run = ScenarioRun(
        scenario_id="s3",
        observed=[
            ObservedTurn(
                turn_index=0,
                result={"turn_relation": "new"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            ),
            ObservedTurn(
                turn_index=1,
                result={"turn_relation": "new", "response_kind": "answer"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
                duplicate=False,
            ),
        ],
        final_state={},
    )
    metrics, _ = evaluate_turn_topic([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["restart_recovery"].score == 1.0
    assert by_name["restart_recovery"].numerator == 1
    assert by_name["restart_recovery"].denominator == 1


def test_clarification_completion_score_1():
    """A scenario with a pending clarification resolved on the final turn -> score 1.0."""
    scenario = MultiturnScenario(
        id="s4",
        turns=[
            ScenarioTurn(input="t0", expected=ExpectedTurn(
                topic_transition="restored",
            )),
            ScenarioTurn(input="t1", expected=ExpectedTurn(
                turn_relation="continue",
            )),
        ],
    )
    run = ScenarioRun(
        scenario_id="s4",
        observed=[
            ObservedTurn(
                turn_index=0,
                result={"turn_relation": "ambiguous"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            ),
            ObservedTurn(
                turn_index=1,
                result={"turn_relation": "continue", "response_kind": "answer"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            ),
        ],
        final_state={},
    )
    metrics, _ = evaluate_turn_topic([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["clarification_completion"].score == 1.0
    assert by_name["clarification_completion"].numerator == 1
    assert by_name["clarification_completion"].denominator == 1


def test_clarification_completion_via_reply_contains():
    """Clarification triggered by reply_contains='clarification' instead of topic_transition."""
    scenario = MultiturnScenario(
        id="s5",
        turns=[
            ScenarioTurn(input="t0", expected=ExpectedTurn(
                reply_contains=["clarification"],
            )),
            ScenarioTurn(input="t1", expected=ExpectedTurn(
                turn_relation="revise",
            )),
        ],
    )
    run = ScenarioRun(
        scenario_id="s5",
        observed=[
            ObservedTurn(
                turn_index=0,
                result={"turn_relation": "ambiguous"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            ),
            ObservedTurn(
                turn_index=1,
                result={"turn_relation": "revise", "response_kind": "answer"},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            ),
        ],
        final_state={},
    )
    metrics, _ = evaluate_turn_topic([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["clarification_completion"].score == 1.0
    assert by_name["clarification_completion"].numerator == 1
    assert by_name["clarification_completion"].denominator == 1
