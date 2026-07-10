from __future__ import annotations

from eval.memory_eval.metrics.memory_lifecycle import evaluate_memory_lifecycle
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _run(expected_ops, observed_ops, active_keys=None, sensitive=0):
    scenario = MultiturnScenario(
        id="m1",
        turns=[
            ScenarioTurn(
                input=f"t{i}",
                expected=ExpectedTurn(
                    memory_operation=exp,
                    active_memory_keys=([k] for k in (active_keys or [])).__next__() if False else [],
                    sensitive_persisted=sensitive if i == 0 else None,
                ),
            )
            for i, exp in enumerate(expected_ops)
        ],
    )
    run = ScenarioRun(
        scenario_id="m1",
        observed=[
            ObservedTurn(
                turn_index=i,
                result={"memory_operation": obs},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            )
            for i, obs in enumerate(observed_ops)
        ],
        final_state={},
    )
    return [(scenario, run)]


def test_explicit_operation_accuracy():
    metrics, _ = evaluate_memory_lifecycle(_run(["remember", "forget"], ["remember", "forget"]))
    by_name = {m.name: m for m in metrics}
    assert by_name["explicit_operation_accuracy"].score == 1.0


def test_prohibited_memory_write_count_gate():
    metrics, _ = evaluate_memory_lifecycle(_run(["remember"], ["remember"], sensitive=1))
    by_name = {m.name: m for m in metrics}
    assert by_name["prohibited_memory_write_count"].numerator == 1
    # score=1.0, at_most, threshold=0.0 → 1.0 <= 0.0 is False (fail closed)
    assert by_name["prohibited_memory_write_count"].passes is False


def test_prohibited_memory_write_count_no_violation():
    metrics, _ = evaluate_memory_lifecycle(_run(["remember"], ["remember"], sensitive=0))
    by_name = {m.name: m for m in metrics}
    pmw = by_name["prohibited_memory_write_count"]
    assert pmw.numerator == 0
    # score=0.0, at_most, threshold=0.0 → 0.0 <= 0.0 is True (passes)
    assert pmw.passes is True
