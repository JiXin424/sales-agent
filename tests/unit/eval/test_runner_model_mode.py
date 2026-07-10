"""Unit tests for the model-multiturn mode + 3x repetition classifier (Spec 4 Task 13)."""
from __future__ import annotations

import pytest

from eval.memory_eval.runner import main, _is_ambiguous_boundary, classify_repetitions
from eval.memory_eval.schema import ExpectedTurn, MultiturnScenario, ScenarioTurn


def test_model_multiturn_requires_tenant():
    # No credentials -> invalid execution return code 2, not a quality failure.
    rc = main(["model-multiturn", "--dataset", "eval/memory/datasets/multiturn_v1.jsonl",
               "--output", "/tmp/x", "--no-credentials"])
    assert rc == 2


def test_classify_repetitions_all_branches():
    # empty -> "na"
    assert classify_repetitions([]) == "na"
    # all agree -> "consistent"
    assert classify_repetitions([["new"], ["new"], ["new"]]) == "consistent"
    # disagree -> "flaky"
    assert classify_repetitions([["new"], ["continue"], ["new"]]) == "flaky"


def _scenario(*, turn_relation="continue", tags=None):
    return MultiturnScenario(
        id="s1",
        tags=tags or [],
        turns=[ScenarioTurn(input="x", expected=ExpectedTurn(turn_relation=turn_relation))],
    )


def test_is_ambiguous_boundary_ambiguous_turn_relation():
    # A turn declaring turn_relation="ambiguous" needs 3x repetitions (§3.3).
    assert _is_ambiguous_boundary(_scenario(turn_relation="ambiguous")) is True


def test_is_ambiguous_boundary_tag():
    # A "boundary" scenario tag needs 3x repetitions (§3.3).
    assert _is_ambiguous_boundary(_scenario(tags=["boundary"])) is True


def test_is_ambiguous_boundary_neutral_is_false():
    # A neutral scenario with no ambiguous relation and no boundary tag -> False.
    assert _is_ambiguous_boundary(_scenario(turn_relation="continue")) is False
