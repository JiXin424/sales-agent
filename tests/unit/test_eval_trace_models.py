"""Test eval trace models and extended eval schema registration."""

import pytest
from sales_agent.core.database import Base


def test_eval_trace_tables_are_registered():
    """Metric results, retrieval traces, and trace hits must be in Base.metadata."""
    expected = {
        "eval_metric_results",
        "retrieval_traces",
        "retrieval_trace_hits",
    }
    actual = set(Base.metadata.tables)
    missing = expected - actual
    assert not missing, f"Missing trace tables: {missing}"


def test_metric_applicability_is_persisted():
    """eval_metric_results must have an applicability column."""
    columns = Base.metadata.tables["eval_metric_results"].c
    assert "applicability" in columns


def test_retrieval_trace_has_tenant_and_profile():
    """retrieval_traces must include tenant_id and retrieval_profile_id."""
    columns = Base.metadata.tables["retrieval_traces"].c
    assert "tenant_id" in columns
    assert "retrieval_profile_id" in columns
    assert "original_query" in columns


def test_retrieval_trace_hits_has_channel_and_rank():
    """retrieval_trace_hits must include channel, channel_rank, final_rank."""
    columns = Base.metadata.tables["retrieval_trace_hits"].c
    assert "channel" in columns
    assert "channel_rank" in columns
    assert "final_rank" in columns
    assert "selected_for_context" in columns


def test_eval_suites_has_new_version_columns():
    """eval_suites extended with suite_type, version, knowledge_version_id."""
    columns = Base.metadata.tables["eval_suites"].c
    assert "suite_type" in columns
    assert "version" in columns
    assert "knowledge_version_id" in columns


def test_eval_cases_has_question_type_and_lineage():
    """eval_cases extended with question_type, role_type, lineage."""
    columns = Base.metadata.tables["eval_cases"].c
    assert "question_type" in columns
    assert "expected_route" in columns
    assert "role_type" in columns
    assert "quality_status" in columns


def test_eval_runs_has_iteration_and_candidate():
    """eval_runs extended with iteration_id, candidate_id, run_type."""
    columns = Base.metadata.tables["eval_runs"].c
    assert "iteration_id" in columns
    assert "candidate_id" in columns
    assert "run_type" in columns


def test_eval_run_results_has_route_and_fact_coverage():
    """eval_run_results extended with actual output, fact coverage, token usage."""
    columns = Base.metadata.tables["eval_run_results"].c
    assert "actual_output_json" in columns
    assert "fact_coverage" in columns
    assert "error_code" in columns
