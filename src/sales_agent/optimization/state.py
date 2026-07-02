"""Optimization LangGraph state definition."""

from __future__ import annotations

from typing import Annotated, Any
from operator import add
from typing_extensions import TypedDict


class OptimizationState(TypedDict, total=False):
    """State flowing through the optimization LangGraph workflow.

    Stages: baseline -> diagnose -> propose -> build -> targeted_eval ->
    regression_eval -> awaiting_approval -> publish -> question_evolution.
    """

    # Input
    tenant_id: str
    agent_id: str
    iteration_id: str
    baseline_release_id: str
    fixed_suite_id: str
    exploration_suite_id: str

    # Baseline
    baseline_eval_run_id: str | None
    baseline_metrics_json: str

    # Diagnosis
    diagnoses_json: str  # list of FailureDiagnosis dicts
    diagnosis_status: str  # "completed" | "needs_human"

    # Proposal
    candidates_json: str  # list of candidate dicts
    candidate_count: int
    consecutive_failures: int

    # Build
    sandbox_knowledge_version_id: str | None
    sandbox_retrieval_profile_id: str | None
    sandbox_router_profile_id: str | None

    # Evaluation
    targeted_eval_run_id: str | None
    sibling_eval_run_id: str | None
    fixed_eval_run_id: str | None
    safety_eval_run_id: str | None
    cross_tenant_eval_run_id: str | None
    eval_comparisons_json: str  # list of EvalComparison dicts

    # Gates
    gate_passed: bool
    gate_hard_failures_json: str
    gate_soft_warnings_json: str

    # Approval
    approval_status: str  # "pending" | "approved" | "rejected"
    approved_by: str | None

    # Publication
    published_release_id: str | None
    rollback_release_id: str | None

    # Control
    stage: str
    status: str  # "running" | "completed" | "failed" | "needs_human"
    error: str | None

    # Log
    log_messages: Annotated[list[str], add]
