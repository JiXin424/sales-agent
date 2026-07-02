"""Test report renderers produce correct, deterministic output."""

import json
import pytest
from sales_agent.optimization.reporting.renderers import (
    render_json,
    render_markdown,
    render_html,
    render_csv,
    RENDERERS,
)


SAMPLE_REPORT = {
    "report_id": "r1",
    "report_type": "candidate",
    "recommendation": "improved",
    "effect_index_before": 70.0,
    "effect_index_after": 82.5,
    "effect_index_delta": 12.5,
    "hard_gates": {"failed": []},
    "formula_version": "effect-v1",
    "data_snapshot_hash": "abc123",
    "created_at": "2026-07-02T00:00:00",
    "groups": [
        {
            "group_name": "answer_quality",
            "weight": 0.30,
            "score_before": 70.0,
            "score_after": 80.0,
            "delta": 10.0,
            "coverage": 3,
            "total_metrics": 3,
            "metrics": [
                {
                    "metric_name": "answer_correctness",
                    "direction": "higher",
                    "before_value": 0.7,
                    "after_value": 0.8,
                    "delta": 0.1,
                    "gate_result": None,
                },
            ],
        },
    ],
    "cases": [
        {
            "case_id": "case1",
            "classification": "improved",
            "cause": "score_improvement",
            "before_pass": True,
            "after_pass": True,
            "score_delta": 0.1,
            "rank_delta": None,
            "latency_delta_ms": None,
            "token_delta": None,
        },
    ],
}


class TestRenderers:
    def test_json_contains_report_id(self):
        output = render_json(SAMPLE_REPORT)
        data = json.loads(output)
        assert data["report_id"] == "r1"

    def test_markdown_contains_report_id(self):
        output = render_markdown(SAMPLE_REPORT)
        assert "r1" in output
        assert "## Effect Index" in output
        assert "improved" in output

    def test_html_escapes_and_embeds_json(self):
        output = render_html(SAMPLE_REPORT)
        assert "<!DOCTYPE html>" in output
        assert "r1" in output
        assert 'id="report-data"' in output

    def test_csv_has_summary_and_metric_rows(self):
        output = render_csv(SAMPLE_REPORT)
        assert "summary" in output
        assert "metric" in output
        assert "case" in output
        assert "answer_correctness" in output

    def test_all_formats_registered(self):
        assert set(RENDERERS.keys()) == {"json", "markdown", "html", "csv"}

    def test_html_escapes_user_content(self):
        report = {**SAMPLE_REPORT, "report_id": "<script>alert(1)</script>"}
        output = render_html(report)
        assert "<script>alert(1)</script>" not in output
        assert "&lt;script&gt;" in output
