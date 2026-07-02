"""Test optimization LangGraph routing logic."""

import pytest
from sales_agent.optimization.nodes import (
    route_after_diagnose,
    route_after_regression,
    route_after_approval,
)


def test_graph_routes_human_only_diagnosis_to_review():
    """Diagnoses that require human review skip proposal."""
    state = {
        "diagnosis_status": "needs_human",
        "diagnoses_json": '[{"primary_cause":"generation_issue","recommended_action":"human_review"}]',
    }
    assert route_after_diagnose(state) == "awaiting_approval"


def test_graph_routes_automatable_diagnosis_to_propose():
    """Automatable diagnoses proceed to proposal."""
    state = {
        "diagnosis_status": "completed",
        "diagnoses_json": '[{"primary_cause":"route_miss","recommended_action":"update_router"}]',
    }
    assert route_after_diagnose(state) == "propose"


def test_graph_no_diagnoses_ends():
    state = {
        "diagnosis_status": "completed",
        "diagnoses_json": "[]",
    }
    assert route_after_diagnose(state) == "end"


def test_graph_gate_passed_routes_to_approval():
    state = {"gate_passed": True}
    assert route_after_regression(state) == "awaiting_approval"


def test_graph_gate_failed_with_budget_routes_to_propose():
    state = {"gate_passed": False, "candidate_count": 1}
    assert route_after_regression(state) == "propose"


def test_graph_gate_failed_no_budget_ends():
    state = {"gate_passed": False, "candidate_count": 3}
    assert route_after_regression(state) == "end"


def test_graph_approval_approved_publishes():
    assert route_after_approval({"approval_status": "approved"}) == "publish"


def test_graph_approval_not_approved_ends():
    assert route_after_approval({"approval_status": "rejected"}) == "end"
