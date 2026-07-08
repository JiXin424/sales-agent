"""Unit tests for the memory-eval CLI dispatcher and report assembly (Spec 4 Task 12)."""
from __future__ import annotations

import pytest

from eval.memory_eval.runner import assemble_report, build_scripts_from_scenarios, main
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)
from eval.memory_eval.versions import collect_version_bundle


def test_unknown_mode_exits_invalid():
    # argparse rejects unknown subcommands by raising SystemExit (invalid execution).
    with pytest.raises(SystemExit):
        main(["bogus", "--output", "/tmp/x"])


def test_build_scripts_uses_expected_relation():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(
        input="记住我负责华东区",
        expected=ExpectedTurn(turn_relation="new", standalone_query_contains=["华东"]),
    )])
    scripts = build_scripts_from_scenarios([s])
    key = ("s1", 0)
    assert key in scripts
    assert scripts[key].context_decision["turn_relation"] == "new"
    assert "华东" in scripts[key].context_decision["standalone_query"]


def test_assemble_report_groups_metrics():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(
        input="t0", expected=ExpectedTurn(turn_relation="new"))])
    run = ScenarioRun(scenario_id="s1", observed=[ObservedTurn(
        turn_index=0, result={"turn_relation": "new"},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={"outcome_ok": True})
    report = assemble_report([(s, run)], collect_version_bundle(dataset_version="t"))
    assert "deterministic_state_safety" in report.groups
    assert report.thresholds_met is True
