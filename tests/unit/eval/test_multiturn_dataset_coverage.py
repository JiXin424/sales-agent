# tests/unit/eval/test_multiturn_dataset_coverage.py
from __future__ import annotations

from pathlib import Path

from eval.memory_eval.dataset import load_scenarios, validate_dataset

DATASET = Path("eval/memory/datasets/multiturn_v1.jsonl")

REQUIRED_TAG_GROUPS = {
    "turn_relation_classes": {"new", "switch", "continue", "revise", "ambiguous"},
    "expiry_restore": {"expiry", "restore", "clarification"},
    "guided_flow": {"guided_flow", "preemption"},
    "explicit_inferred": {"explicit", "inferred", "remember"},
    "correct_forget_expiry": {"correct", "forget"},
    "profile_recall": {"profile_recall", "profile_no_recall"},
    "cross_scope": {"cross_scope_isolation"},
    "restart_duplicate_concurrency": {"restart", "duplicate", "concurrency"},
    "degradation": {"degradation"},
}


def test_dataset_has_40_plus_scenarios_and_full_coverage():
    scenarios = load_scenarios(str(DATASET))
    assert len(scenarios) >= 40, f"need >=40 scenarios, got {len(scenarios)}"
    errors = validate_dataset(scenarios)
    assert not errors, f"dataset invalid: {errors}"

    all_tags = set()
    for s in scenarios:
        all_tags.update(s.tags)
    for group, required in REQUIRED_TAG_GROUPS.items():
        missing = required - all_tags
        assert not missing, f"{group} missing tags: {missing}"
