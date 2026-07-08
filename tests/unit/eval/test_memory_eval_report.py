# tests/unit/eval/test_memory_eval_report.py
from __future__ import annotations

import json
from pathlib import Path

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.report import EvaluationReport, write_report
from eval.memory_eval.versions import VersionBundle


def _vb() -> VersionBundle:
    return VersionBundle(
        model_version="qwen-plus",
        prompt_version="db-managed",
        code_version="0.1.0-dev",
        dataset_version="multiturn_v1",
        knowledge_version="unset",
        memory_schema_version="0013",
        generator_version="memory_eval_v1",
    )


def test_write_report_emits_json_and_markdown(tmp_path: Path):
    report = EvaluationReport(versions=_vb(), total_scenarios=2, total_turns=4)
    report.add_metric(
        "deterministic_state_safety",
        MetricResult(name="turn_relation_accuracy", numerator=4, denominator=4, score=1.0, threshold=0.9),
    )
    out = write_report(report, str(tmp_path / "out"))
    assert (out / "report.json").exists()
    assert (out / "report.md").exists()
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["versions"]["dataset_version"] == "multiturn_v1"
    grp = data["groups"]["deterministic_state_safety"]
    assert grp[0]["applicable"] is True
    assert grp[0]["numerator"] == 4
    assert grp[0]["denominator"] == 4
    assert grp[0]["threshold"] == 0.9


def test_error_metric_does_not_flip_pass(tmp_path: Path):
    report = EvaluationReport(versions=_vb())
    report.add_metric(
        "semantic_answer_quality",
        MetricResult(name="relevance", score=0.0, error="judge timeout"),
    )
    out = write_report(report, str(tmp_path / "err"))
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["groups"]["semantic_answer_quality"][0]["error"] == "judge timeout"
    assert data["thresholds_met"] is True  # error never flips gate (§10)
