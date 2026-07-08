"""Unit tests for the model-multiturn mode + 3x repetition classifier (Spec 4 Task 13)."""
from __future__ import annotations

import pytest

from eval.memory_eval.runner import main


def test_model_multiturn_requires_tenant():
    # No credentials -> invalid execution return code 2, not a quality failure.
    rc = main(["model-multiturn", "--dataset", "eval/memory/datasets/multiturn_v1.jsonl",
               "--output", "/tmp/x", "--no-credentials"])
    assert rc == 2


def test_consistency_report_flags_disagreement():
    from eval.memory_eval.runner import classify_repetitions
    assert classify_repetitions([["new"], ["new"], ["new"]]) == "consistent"
    assert classify_repetitions([["new"], ["continue"], ["new"]]) == "flaky"
