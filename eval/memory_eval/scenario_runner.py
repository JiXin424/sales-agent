"""Scenario runner: drives the real Online Graph turn-by-turn (Spec 4 §3.2, §4)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from eval.memory_eval.schema import (
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
)

logger = logging.getLogger(__name__)

InvokeTurn = Callable[..., Awaitable[dict[str, Any]]]
CaptureState = Callable[[Any, int, dict[str, Any]], Awaitable[ObservedTurn]]
RestartRuntime = Callable[[], Awaitable[None]]


class ScenarioRunner:
    """Runs one ``MultiturnScenario`` through an injectable turn invoker.

    The default invoker binds to ``invoke_online_turn`` against the production
    Online Graph; tests inject a fake to unit-test orchestration.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        invoke_turn: Optional[InvokeTurn] = None,
        capture_state: Optional[CaptureState] = None,
        restart_runtime: Optional[RestartRuntime] = None,
        now_provider: Callable[[], Any] = lambda: None,
    ) -> None:
        self.ctx = ctx
        self._invoke_turn = invoke_turn or _default_invoke_turn
        self._capture = capture_state or _default_capture
        self._restart = restart_runtime
        self._now = now_provider

    async def run(self, scenario: MultiturnScenario) -> ScenarioRun:
        observed: list[ObservedTurn] = []
        prev_event_id: Optional[str] = None
        accumulated_offset = 0
        last_result: dict[str, Any] = {}

        for i, turn in enumerate(scenario.turns):
            accumulated_offset += turn.time_offset_seconds

            event_id = prev_event_id if turn.duplicate_previous_event else (
                turn.event_id or f"{scenario.id}-{i}"
            )
            now = self._advanced_now(accumulated_offset)

            # Advance the deterministic ScriptedModelDouble (Task 10) so it
            # serves the script for THIS turn rather than a sticky prior one.
            # Real models have no ``set_turn`` -> skipped (duck-typed).
            chat_model = self.ctx.get("chat_model")
            if chat_model is not None and hasattr(chat_model, "set_turn"):
                chat_model.set_turn(scenario.id, i)

            try:
                if turn.restart_before and self._restart is not None:
                    await self._restart()
                if turn.concurrent_group:
                    result = await self._invoke_concurrent(scenario, i, turn, event_id, now)
                else:
                    result = await self._invoke_turn(
                        self.ctx, message=turn.input, event_id=event_id, now=now,
                        chat_model=chat_model,
                    )
                last_result = result
                observed.append(await self._capture(self.ctx, i, result))
            except Exception as exc:  # noqa: BLE001 — invalidate the run, not a product score (§10)
                logger.exception("scenario %s turn %d failed", scenario.id, i)
                observed.append(ObservedTurn(
                    turn_index=i, result={}, replies=[], active_topic_ids=[],
                    closed_topic_ids=[], active_memory_keys=[], selected_memory_ids=[],
                    error=str(exc),
                ))
                return ScenarioRun(scenario_id=scenario.id, observed=observed,
                                   final_state=last_result, error=str(exc))
            if not turn.duplicate_previous_event:
                prev_event_id = event_id

        return ScenarioRun(scenario_id=scenario.id, observed=observed, final_state=last_result)

    def _advanced_now(self, offset_seconds: int) -> Any:
        base = self._now()
        if base is None:
            return None
        try:
            from datetime import timedelta
            return base + timedelta(seconds=offset_seconds)
        except Exception:  # pragma: no cover - base is not a datetime
            return base

    async def _invoke_concurrent(self, scenario, i, turn, event_id, now):
        """Within a concurrent group, turns serialize via the per-thread
        advisory lock (``acquire_online_turn_lock``). The runner simply awaits
        them on the same thread_id; the lock guarantees no overlap."""
        return await self._invoke_turn(
            self.ctx, message=turn.input, event_id=event_id, now=now,
            chat_model=self.ctx.get("chat_model"),
        )


async def _default_invoke_turn(ctx, *, message, event_id, now, chat_model):
    """Bind to the real Online Graph entry."""
    from sales_agent.services.online_conversation import invoke_online_turn
    return await invoke_online_turn(
        db=ctx["db"],
        tenant_id=ctx["tenant_id"],
        agent_id=ctx["agent_id"],
        user_id=ctx["user_id"],
        session_user_id=ctx["session_user_id"],
        channel=ctx["channel"],
        conversation_id=ctx["conversation_id"],
        message=message,
        event_id=event_id,
        chat_model=chat_model,
        embedding_model=ctx.get("embedding_model"),
        now=now,
    )


async def _default_capture(ctx, turn_index, result):
    """Capture observed state from the graph result + DB."""
    from sqlalchemy import select
    from sales_agent.models.atomic_memory import AtomicMemory
    db = ctx["db"]
    scope = (ctx["tenant_id"], ctx["agent_id"], ctx["user_id"])
    rows = (await db.execute(
        select(AtomicMemory.normalized_key).where(
            AtomicMemory.tenant_id == scope[0],
            AtomicMemory.agent_id == scope[1],
            AtomicMemory.subject_id == scope[2],
            AtomicMemory.status == "active",
        )
    )).scalars().all()
    return ObservedTurn(
        turn_index=turn_index,
        result=result,
        replies=result.get("replies", []),
        active_topic_ids=result.get("active_topic_ids", []),
        closed_topic_ids=result.get("closed_topic_ids", []),
        active_memory_keys=list(rows),
        selected_memory_ids=result.get("selected_memory_ids", []),
        profile_version=result.get("profile_version"),
        duplicate=result.get("response_kind") == "duplicate",
    )


__all__ = ["ScenarioRunner"]
