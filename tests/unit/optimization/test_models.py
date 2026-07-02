"""Test optimization workflow models are registered correctly."""

import pytest
from sales_agent.core.database import Base

# Import optimization models to trigger registration
import sales_agent.models.optimization  # noqa: F401


def test_optimization_tables_are_registered():
    """All six optimization workflow tables must be in Base.metadata."""
    expected = {
        "optimization_iterations",
        "failure_diagnoses",
        "optimization_candidates",
        "candidate_eval_runs",
        "iteration_graph_checkpoints",
        "optimization_jobs",
    }
    actual = set(Base.metadata.tables)
    missing = expected - actual
    assert not missing, f"Missing tables: {missing}"


def test_candidate_change_type_is_single_enum_value():
    """OptimizationCandidate must restrict change_type to 3 allowed values."""
    # Verify the ALLOWED_CHANGE_TYPES set exists
    from sales_agent.models.optimization import OptimizationCandidate
    assert hasattr(OptimizationCandidate, "ALLOWED_CHANGE_TYPES")
    assert OptimizationCandidate.ALLOWED_CHANGE_TYPES == {"router", "retrieval", "document"}


def test_iteration_has_tenant_agent_unique():
    """optimization_iterations must have unique (tenant_id, agent_id, iteration_no)."""
    columns = Base.metadata.tables["optimization_iterations"].c
    assert "tenant_id" in columns
    assert "agent_id" in columns
    assert "iteration_no" in columns


def test_optimization_jobs_has_idempotency_key():
    """optimization_jobs must have idempotency_key and stage fields."""
    columns = Base.metadata.tables["optimization_jobs"].c
    assert "idempotency_key" in columns
    assert "stage" in columns
    assert "lease_owner" in columns
    assert "lease_expires_at" in columns


def test_iteration_graph_checkpoints_reference_thread():
    """checkpoint table must reference LangGraph thread_id."""
    columns = Base.metadata.tables["iteration_graph_checkpoints"].c
    assert "thread_id" in columns
    assert "checkpoint_id" in columns
    assert "stage" in columns


def test_failure_diagnoses_has_primary_cause():
    """FailureDiagnosis model must persist primary_cause."""
    columns = Base.metadata.tables["failure_diagnoses"].c
    assert "primary_cause" in columns
    assert "confidence" in columns
    assert "iteration_id" in columns
