from pathlib import Path

from eval.run_long_term_memory_eval import load_scenarios, run_fixture_eval


def test_long_term_memory_dataset_has_required_scenarios():
    scenarios = load_scenarios("eval/memory/long_term_atomic_scenarios.jsonl")
    report = run_fixture_eval(scenarios)

    assert report.total_scenarios >= 6
    assert report.explicit_success_rate == 1.0
    assert report.cross_scope_leakage == 0
    assert report.sensitive_persisted == 0
    assert report.thresholds_met is True
