from pathlib import Path

from eval.run_user_profile_memory_eval import load_scenarios, run_fixture_eval


def test_user_profile_memory_fixture_dataset_passes_required_tags():
    scenarios = load_scenarios("eval/memory/user_profile_recall_scenarios.jsonl")
    report = run_fixture_eval(scenarios)

    assert report.total_scenarios >= 6
    assert report.profile_evidence_pass_rate == 1.0
    assert report.budget_violation_count == 0
    assert report.cross_scope_leakage == 0
    assert report.knowledge_override_violations == 0
    assert report.thresholds_met is True
