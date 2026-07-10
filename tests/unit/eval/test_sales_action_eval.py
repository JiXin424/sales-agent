"""Sales action fixture eval - dataset passes the required invariant tags."""

from __future__ import annotations

from eval.run_sales_action_eval import load_scenarios, run_fixture_eval


def test_sales_action_fixture_dataset_passes_required_tags():
    report = run_fixture_eval(load_scenarios("eval/sales_actions/action_scenarios.jsonl"))
    assert report.total_scenarios >= 12
    assert report.clear_create_accuracy == 1.0
    assert report.clarification_accuracy == 1.0
    assert report.duplicate_send_violations == 0
    assert report.memory_pollution_violations == 0
    assert report.thresholds_met is True


def test_sales_action_fixture_eval_flags_a_polluting_scenario():
    """A scenario that expects writes_memory=True must be flagged + fail the gate."""
    scenarios = load_scenarios("eval/sales_actions/action_scenarios.jsonl")
    polluted = list(scenarios) + [
        {
            "id": "bad-pollute",
            "tag": "clear_create",
            "input": "x",
            "expects": {
                "action": "create",
                "creates_task": True,
                "duplicate_send": False,
                "writes_memory": True,  # <-- pollution
            },
        }
    ]
    report = run_fixture_eval(polluted)
    assert report.memory_pollution_violations == 1
    assert report.thresholds_met is False
