"""Iteration event service: atomic append, redaction, replay, and long-poll.

Sequence numbers are allocated with a single UPDATE … RETURNING so they are
monotonic and gap-free within the same Postgres transaction. The caller must
wrap append + state mutation in the same transaction.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationIteration
from sales_agent.models.iteration_observability import IterationEvent
from sales_agent.models.base import generate_id, utcnow

logger = logging.getLogger(__name__)

# ── Redaction ────────────────────────────────────────────────────────────────

_REDACT_KEYS: set[str] = {
    "token", "api_key", "password", "secret", "credential",
    "access_key", "private_key", "authorization",
}


def _redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy *payload*, replacing secret-like values with ``[REDACTED]``.

    Only recurses one level into nested dicts; list items are left as-is.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            out[key] = _redact_payload(value)
        elif key.lower() in _REDACT_KEYS or key.lower().endswith("_token"):
            out[key] = "[REDACTED]"
        else:
            out[key] = value
    return out


# ── Actor ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EventActor:
    """Who produced an event."""

    actor_type: str = "system"  # system | user | worker
    actor_id: str | None = None

    @classmethod
    def system(cls) -> EventActor:
        return cls(actor_type="system")

    @classmethod
    def user(cls, user_id: str) -> EventActor:
        return cls(actor_type="user", actor_id=user_id)

    @classmethod
    def worker(cls, worker_id: str) -> EventActor:
        return cls(actor_type="worker", actor_id=worker_id)


# ── Service ──────────────────────────────────────────────────────────────────

TERMINAL_STATES: set[str] = {
    "completed", "cancelled", "failed", "rolled_back",
}


@dataclass
class WaitResult:
    """Returned by ``wait_after`` to signal current state."""

    events: list[IterationEvent] = field(default_factory=list)
    next_sequence: int = 0
    terminal: bool = False


class IterationEventService:
    """Append-only event stream for a single optimization iteration."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Append ──────────────────────────────────────────────────────────

    async def append(
        self,
        iteration: OptimizationIteration,
        event_type: str,
        *,
        stage: str | None = None,
        status: str | None = None,
        progress: tuple[int, int] | None = None,
        message: str = "",
        payload: Mapping[str, Any] | None = None,
        actor: EventActor = EventActor.system(),
    ) -> IterationEvent:
        """Allocate the next sequence atomically and persist an event.

        The sequence allocation and the event INSERT share the caller's
        transaction.  A rollback discards both.
        """
        # Atomic sequence allocation
        sequence: int = await self.db.scalar(
            update(OptimizationIteration)
            .where(
                OptimizationIteration.id == iteration.id,
                OptimizationIteration.tenant_id == iteration.tenant_id,
            )
            .values(event_sequence=OptimizationIteration.event_sequence + 1)
            .returning(OptimizationIteration.event_sequence)
        )
        # sequence is never None because the WHERE clause matches the row
        assert sequence is not None, "iteration row not found for event append"

        redacted = _redact_payload(payload) if payload else {}

        prog_current, prog_total = progress or (None, None)

        event = IterationEvent(
            id=generate_id(),
            tenant_id=iteration.tenant_id,
            agent_id=iteration.agent_id,
            iteration_id=iteration.id,
            sequence_no=sequence,
            event_type=event_type,
            stage=stage,
            status=status,
            progress_current=prog_current,
            progress_total=prog_total,
            message=message,
            payload_json=json.dumps(redacted, ensure_ascii=False),
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
        )
        self.db.add(event)
        await self.db.flush()
        # Refresh iteration's local event_sequence so further appends see the
        # latest value without a re-fetch.  This is safe because the UPDATE
        # above used RETURNING and the ORM tracks the column.
        iteration.event_sequence = sequence
        return event

    # ── Replay ───────────────────────────────────────────────────────────

    async def list_after(
        self,
        *,
        tenant_id: str,
        iteration_id: str,
        after_sequence: int = 0,
        limit: int = 50,
    ) -> list[IterationEvent]:
        """Return events strictly after *after_sequence*, ordered by seq."""
        result = await self.db.execute(
            select(IterationEvent)
            .where(
                IterationEvent.tenant_id == tenant_id,
                IterationEvent.iteration_id == iteration_id,
                IterationEvent.sequence_no > after_sequence,
            )
            .order_by(IterationEvent.sequence_no.asc())
            .limit(min(limit, 200))
        )
        return list(result.scalars().all())

    # ── Long-poll ────────────────────────────────────────────────────────

    async def wait_after(
        self,
        *,
        tenant_id: str,
        iteration_id: str,
        after_sequence: int = 0,
        timeout_seconds: int = 30,
        limit: int = 100,
    ) -> WaitResult:
        """Long-poll for events after *after_sequence*.

        Returns as soon as events are available or the deadline is reached.
        """
        capped_timeout = min(timeout_seconds, 30)
        deadline = time.monotonic() + capped_timeout
        poll_interval = 0.2  # seconds between polls

        while True:
            events = await self.list_after(
                tenant_id=tenant_id,
                iteration_id=iteration_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            if events:
                next_seq = events[-1].sequence_no
                return WaitResult(events=events, next_sequence=next_seq, terminal=False)

            # Check iteration status for terminal signal
            iteration = await self.db.scalar(
                select(OptimizationIteration).where(
                    OptimizationIteration.id == iteration_id,
                    OptimizationIteration.tenant_id == tenant_id,
                )
            )
            if iteration is not None and iteration.status in TERMINAL_STATES:
                return WaitResult(events=[], next_sequence=after_sequence, terminal=True)

            if time.monotonic() >= deadline:
                return WaitResult(events=[], next_sequence=after_sequence, terminal=False)

            # Release DB connection during wait; the caller's session must not
            # be committed here — we are only reading.
            await self._sleep(poll_interval)

    async def _sleep(self, seconds: float) -> None:
        """Lightweight sleep, replaceable in tests."""
        import asyncio
        await asyncio.sleep(seconds)
