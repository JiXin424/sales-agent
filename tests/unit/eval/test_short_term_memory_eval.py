"""Unit tests for the short-term-memory evaluation runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eval.run_short_term_memory_eval import (
    load_scenarios,
    validate_dataset,
    run_fixture_eval,
    write_report,
    ScenarioReport,
)
from eval.support.dingtalk_scenario import ShortTermScenario


def _write_jsonl(turns: list[dict], tags=None) -> str:
    data = {"id": "test-001", "tags": tags or [], "turns": turns}
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(data, tmp)
    tmp.write("\n")
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# load_scenarios
# ---------------------------------------------------------------------------


class TestLoadScenarios:
    def test_loads_valid_scenario(self):
        path = _write_jsonl([
            {"input": "hello", "expected": {"reply_count": 1}},
            {"input": "world", "expected": {"reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        assert len(scenarios) == 1
        assert scenarios[0].id == "test-001"
        assert len(scenarios[0].turns) == 2

    def test_rejects_unknown_relation_via_validate(self):
        path = _write_jsonl([
            {"input": "a", "expected": {"turn_relation": "invalid_val"}},
            {"input": "b", "expected": {"reply_count": 1}},
        ])
        with pytest.raises(Exception):
            load_scenarios(path)

    def test_rejects_duplicate_id(self):
        path1 = _write_jsonl([
            {"input": "a", "expected": {}}, {"input": "b", "expected": {}}
        ])
        path2 = _write_jsonl([
            {"input": "x", "expected": {}}, {"input": "y", "expected": {}}
        ])
        s1 = load_scenarios(path1)
        s2 = load_scenarios(path2)
        s2[0].id = s1[0].id  # force duplicate
        errors = validate_dataset(s1 + s2)
        assert any("Duplicate" in e for e in errors)

    def test_rejects_first_turn_duplicate(self):
        path = _write_jsonl([
            {"input": "a", "duplicate_previous_event": True, "expected": {"reply_count": 0}},
            {"input": "b", "expected": {"reply_count": 1}},
        ])
        with pytest.raises(Exception):
            load_scenarios(path)

    def test_accepts_single_turn(self):
        path = _write_jsonl([
            {"input": "solo", "expected": {"reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        assert len(scenarios) == 1
        assert len(scenarios[0].turns) == 1

    def test_valid_dataset_passes(self):
        path = _write_jsonl([
            {"input": "hi", "expected": {"reply_count": 1}},
            {"input": "bye", "expected": {"reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        errors = validate_dataset(scenarios)
        assert errors == []


# ---------------------------------------------------------------------------
# fixture evaluation
# ---------------------------------------------------------------------------


class TestFixtureEval:
    def test_clean_dataset_thresholds_met(self):
        path = _write_jsonl([
            {"input": "a", "expected": {"turn_relation": "continue", "reply_count": 1}},
            {"input": "b", "expected": {"turn_relation": "new", "reply_count": 1}},
        ], tags=["continue", "new", "revise", "switch", "ambiguous", "guided_flow", "restart", "duplicate"])
        scenarios = load_scenarios(path)
        report = run_fixture_eval(scenarios)
        assert report.total_scenarios == 1
        assert report.total_turns == 2
        assert report.thresholds_met is True

    def test_missing_tags_reported(self):
        path = _write_jsonl([
            {"input": "a", "expected": {"reply_count": 1}},
            {"input": "b", "expected": {"reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        report = run_fixture_eval(scenarios)
        assert report.thresholds_met is False
        assert any("Missing scenario tags" in f for f in report.failures)

    def test_duplicate_violation_detected(self):
        path = _write_jsonl([
            {"input": "a", "expected": {"reply_count": 1}},
            {"input": "b", "duplicate_previous_event": True, "expected": {"reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        report = run_fixture_eval(scenarios)
        assert report.duplicate_violations > 0

    def test_leakage_detected(self):
        path = _write_jsonl([
            {"input": "a", "expected": {"turn_relation": "continue", "retained_entities": ["x"], "reply_count": 1}},
            {"input": "b", "expected": {"turn_relation": "new", "retained_entities": ["x"], "reply_count": 1}},
        ])
        scenarios = load_scenarios(path)
        report = run_fixture_eval(scenarios)
        assert report.topic_leakage_count > 0


# ---------------------------------------------------------------------------
# report writing
# ---------------------------------------------------------------------------


class TestReportWriting:
    def test_writes_json_and_md(self):
        report = ScenarioReport(
            timestamp="2026-01-01T00:00:00",
            total_scenarios=1, total_turns=2,
            thresholds_met=True,
        )
        with tempfile.TemporaryDirectory() as d:
            write_report(report, d)
            assert Path(d, "report.json").exists()
            assert Path(d, "report.md").exists()
            data = json.loads(Path(d, "report.json").read_text())
            assert data["thresholds_met"] is True
