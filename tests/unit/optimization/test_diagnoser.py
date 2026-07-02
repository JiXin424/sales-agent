"""Test deterministic failure diagnoser across all ordered branches."""

import pytest
from sales_agent.optimization.diagnoser import FailureDiagnoser


def _trace(**overrides):
    """Build a clean trace dict with defaults for a passing case."""
    base = {
        "eval_case_valid": True,
        "route_match": True,
        "expected_route": "knowledge_qa",
        "actual_route": "knowledge_qa",
        "fact_in_corpus": "present",
        "gold_in_candidates": True,
        "gold_in_final": True,
        "context_selected": [{"chunk_id": "c1", "text": "The customer discount is 5% off for bulk orders of 100+ units."}],
        "gold_fact_in_answer": True,
        "case_ids": ["case_1"],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("overrides", "cause"),
    [
        ({"route_match": False, "actual_route": "general_sales_coaching"}, "route_miss"),
        ({"fact_in_corpus": "absent"}, "document_missing"),
        ({"gold_in_candidates": False}, "retrieval_recall"),
        ({"gold_in_final": False}, "retrieval_ranking"),
        ({"context_selected": [{"chunk_id": "c1", "text": "short"}]}, "chunking_or_structure"),
        ({"gold_fact_in_answer": False}, "generation_issue"),
    ],
)
def test_primary_cause_order(overrides, cause):
    """Each trace variant must produce the correct primary cause."""
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(_trace(**overrides))
    assert diagnosis.primary_cause == cause, (
        f"Expected {cause}, got {diagnosis.primary_cause}. "
        f"Evidence: {diagnosis.evidence}"
    )


def test_invalid_eval_case_supersedes_all():
    """An invalid eval case must be diagnosed first, before any other checks."""
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(_trace(
        eval_case_valid=False,
        route_match=False,
        gold_in_candidates=False,
    ))
    assert diagnosis.primary_cause == "invalid_eval_case"


def test_blocked_checks_recorded():
    """Diagnosis must record which downstream checks were blocked."""
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(_trace(route_match=False))
    assert "route_miss" not in diagnosis.blocked_checks
    # Checks below route_miss should appear as blocked because we returned early
    # (blocked is empty since we return at the first match)


def test_confidence_is_assigned():
    """Every diagnosis must have a confidence score."""
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(_trace(route_match=False))
    assert diagnosis.confidence > 0


def test_recommended_action_matches_cause():
    """Each cause maps to a recommended action."""
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(_trace(fact_in_corpus="absent"))
    assert diagnosis.recommended_action == "add_document"
