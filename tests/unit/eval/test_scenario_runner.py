from __future__ import annotations

import pytest

from eval.memory_eval.scenario_runner import ScenarioRunner
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ScenarioTurn,
)


def _scenario():
    return MultiturnScenario(id="s1", turns=[
        ScenarioTurn(input="t0", event_id="e0", expected=ExpectedTurn(turn_relation="new")),
        ScenarioTurn(input="t1", duplicate_previous_event=True, expected=ExpectedTurn()),
        ScenarioTurn(input="t2", restart_before=True, time_offset_seconds=3600, expected=ExpectedTurn()),
    ])


@pytest.mark.asyncio
async def test_duplicate_reuses_previous_event_id():
    seen_event_ids: list[str] = []

    async def fake_invoke(ctx, *, message, event_id, now, chat_model):
        seen_event_ids.append(event_id)
        return {"turn_relation": "new" if message == "t0" else "duplicate"}

    async def fake_capture(ctx, turn_index, result):
        from eval.memory_eval.schema import ObservedTurn
        return ObservedTurn(
            turn_index=turn_index, result=result, replies=[],
            active_topic_ids=[], closed_topic_ids=[], active_memory_keys=[],
            selected_memory_ids=[], duplicate=result.get("turn_relation") == "duplicate",
        )

    runner = ScenarioRunner(
        ctx={}, invoke_turn=fake_invoke, capture_state=fake_capture,
        now_provider=lambda: 0,
    )
    run = await runner.run(_scenario())
    # turn 1 duplicates turn 0's event id
    assert seen_event_ids[1] == seen_event_ids[0]
    assert seen_event_ids[2] != seen_event_ids[0]
    assert run.observed[1].duplicate is True


@pytest.mark.asyncio
async def test_restart_and_time_offset_applied():
    from datetime import datetime, timedelta, timezone
    restarts: list[bool] = []
    nows: list = []
    base = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)

    async def fake_invoke(ctx, *, message, event_id, now, chat_model):
        nows.append(now)
        return {"turn_relation": "continue"}

    async def fake_capture(ctx, turn_index, result):
        from eval.memory_eval.schema import ObservedTurn
        return ObservedTurn(
            turn_index=turn_index, result=result, replies=[],
            active_topic_ids=[], closed_topic_ids=[], active_memory_keys=[],
            selected_memory_ids=[],
        )

    async def fake_restart():
        restarts.append(True)

    runner = ScenarioRunner(
        ctx={},
        invoke_turn=fake_invoke,
        capture_state=fake_capture,
        now_provider=lambda: base,
        restart_runtime=fake_restart,
    )
    await runner.run(_scenario())
    assert restarts == [True]                                  # restart_before on turn 2
    assert nows[2] == base + timedelta(seconds=3600)           # time_offset_seconds advanced `now`


@pytest.mark.asyncio
async def test_chat_model_set_turn_advanced_per_turn():
    """The runner must duck-type ``chat_model.set_turn(scenario_id, turn_index)``
    before each invoke so the Task 10 deterministic double advances its script
    (real models have no ``set_turn`` -> skipped)."""
    set_turn_calls: list[tuple[str, int]] = []

    class FakeChatModel:
        def set_turn(self, scenario_id: str, turn_index: int) -> None:
            set_turn_calls.append((scenario_id, turn_index))

    two_turn = MultiturnScenario(id="s1", turns=[
        ScenarioTurn(input="a", expected=ExpectedTurn()),
        ScenarioTurn(input="b", expected=ExpectedTurn()),
    ])

    async def fake_invoke(ctx, *, message, event_id, now, chat_model):
        return {"turn_relation": "new"}

    async def fake_capture(ctx, turn_index, result):
        from eval.memory_eval.schema import ObservedTurn
        return ObservedTurn(
            turn_index=turn_index, result=result, replies=[],
            active_topic_ids=[], closed_topic_ids=[], active_memory_keys=[],
            selected_memory_ids=[],
        )

    runner = ScenarioRunner(
        ctx={"chat_model": FakeChatModel()},
        invoke_turn=fake_invoke,
        capture_state=fake_capture,
        now_provider=lambda: None,
    )
    await runner.run(two_turn)
    assert set_turn_calls == [("s1", 0), ("s1", 1)]
