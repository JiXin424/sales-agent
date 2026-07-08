"""Integration tests: DingTalk multi-turn memory scenarios.

Validates the 24 Spec-1 short-term-memory scenarios against the production
scenario schema and fixture-mode evaluation gate.

Model-backed evaluation requires --mode model + --tenant-id.
"""

from __future__ import annotations

import pytest

from eval.run_short_term_memory_eval import load_scenarios, validate_dataset, run_fixture_eval


@pytest.mark.integration
def test_load_24_scenarios_structure():
    """All 24 short-term scenarios parse without validation errors."""
    scenarios = load_scenarios("eval/memory/short_term_scenarios.jsonl")
    assert len(scenarios) == 24
    total_turns = sum(len(s.turns) for s in scenarios)
    assert total_turns == 52


@pytest.mark.integration
def test_scenario_dataset_passes_fixture_gate():
    """The 24-scenario dataset passes the fixture-mode evaluation gate."""
    scenarios = load_scenarios("eval/memory/short_term_scenarios.jsonl")
    errors = validate_dataset(scenarios)
    assert errors == [], f"Unexpected validation errors: {errors}"
    report = run_fixture_eval(scenarios)
    assert report.thresholds_met, f"Thresholds not met: {report.failures}"
    assert report.topic_leakage_count == 0
    assert report.duplicate_violations == 0


@pytest.mark.integration
def test_all_required_classes_covered():
    """Every turn_relation value and scenario tag set is represented."""
    scenarios = load_scenarios("eval/memory/short_term_scenarios.jsonl")
    present = set()
    for s in scenarios:
        for t in s.turns:
            if t.expected.turn_relation:
                present.add(t.expected.turn_relation)
    required = {"continue", "revise", "switch", "new", "ambiguous"}
    missing = required - present
    assert missing == set(), f"Missing turn_relation classes: {missing}"
