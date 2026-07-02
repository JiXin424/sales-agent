"""Test iteration observability models are registered correctly."""

import pytest
from sales_agent.core.database import Base

# Import observability models to trigger registration
import sales_agent.models.iteration_observability  # noqa: F401


def test_observability_tables_and_tenant_keys():
    """All four observability tables must be registered with tenant_id."""
    expected = {
        "iteration_events",
        "iteration_reports",
        "iteration_report_metrics",
        "iteration_report_cases",
    }
    tables = set(Base.metadata.tables)
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"
    for name in expected:
        assert "tenant_id" in Base.metadata.tables[name].c, (
            f"{name} missing tenant_id column"
        )


def test_iteration_has_atomic_event_cursor_and_final_report_refs():
    """optimization_iterations must have event_sequence, current_stage,
    post_publish_eval_run_id, and final_report_id columns."""
    columns = Base.metadata.tables["optimization_iterations"].c
    required = {
        "event_sequence",
        "current_stage",
        "post_publish_eval_run_id",
        "final_report_id",
    }
    missing = required - set(columns.keys())
    assert not missing, f"Missing iteration columns: {missing}"


def test_iteration_events_has_sequence_and_stage():
    """iteration_events must have sequence_no, stage, and payload_json."""
    columns = Base.metadata.tables["iteration_events"].c
    assert "sequence_no" in columns
    assert "event_type" in columns
    assert "stage" in columns
    assert "payload_json" in columns
    assert "actor_type" in columns


def test_iteration_reports_has_candidate_key_and_hash():
    """iteration_reports must have candidate_key, formula_version,
    data_snapshot_hash, and recommendation."""
    columns = Base.metadata.tables["iteration_reports"].c
    assert "candidate_key" in columns
    assert "formula_version" in columns
    assert "data_snapshot_hash" in columns
    assert "recommendation" in columns
    assert "report_type" in columns


def test_report_metrics_has_direction_and_weight():
    """iteration_report_metrics must have direction, weight, and gate_result."""
    columns = Base.metadata.tables["iteration_report_metrics"].c
    assert "direction" in columns
    assert "weight" in columns
    assert "gate_result" in columns
    assert "group_name" in columns


def test_report_cases_has_classification_and_deltas():
    """iteration_report_cases must have classification and delta columns."""
    columns = Base.metadata.tables["iteration_report_cases"].c
    assert "classification" in columns
    assert "rank_delta" in columns
    assert "score_delta" in columns
