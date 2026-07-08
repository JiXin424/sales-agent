from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from sales_agent.core.database import get_session_factory
from sales_agent.models.atomic_memory import MemoryOutboxJob
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.extractor import extract_memory_candidates
from sales_agent.services.memory.repository import AtomicMemoryRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxProcessResult:
    candidate_count: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def process_outbox_payload(
    *,
    repo: AtomicMemoryRepository,
    chat_model,
    payload: dict,
) -> OutboxProcessResult:
    scope = MemoryScope(
        tenant_id=payload["tenant_id"],
        agent_id=payload["agent_id"],
        user_id=payload["user_id"],
    )
    candidates = await extract_memory_candidates(
        user_message=payload["user_message"],
        topic_summary=payload.get("topic_summary", ""),
        verified_tool_facts=payload.get("verified_tool_facts", []),
        chat_model=chat_model,
    )
    for candidate in candidates:
        await repo.corroborate_candidate(
            scope,
            candidate,
            conversation_id=payload["conversation_id"],
            message_id=payload["message_id"],
        )
    return OutboxProcessResult(candidate_count=len(candidates))


async def run_memory_outbox_once(*, session_factory, chat_model, batch_size: int, max_attempts: int) -> int:
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(MemoryOutboxJob)
                    .where(MemoryOutboxJob.status == "pending")
                    .where(MemoryOutboxJob.available_at <= _now())
                    .order_by(MemoryOutboxJob.created_at.asc())
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

        processed = 0
        for row in rows:
            repo = AtomicMemoryRepository(db)
            try:
                await process_outbox_payload(
                    repo=repo,
                    chat_model=chat_model,
                    payload=json.loads(row.payload_json),
                )
                row.status = "done"
                row.last_error = None
                processed += 1
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:1000]
                if row.attempts >= max_attempts:
                    row.status = "dead"
                else:
                    row.status = "pending"
                    row.available_at = _now() + timedelta(seconds=min(300, 2 ** row.attempts))
        await db.commit()
        return processed


async def expire_due_memories_once(*, session_factory) -> int:
    async with session_factory() as db:
        repo = AtomicMemoryRepository(db)
        result = await repo.expire_due_memories(_now())
        await db.commit()
        return result.expired_count


async def memory_outbox_loop(*, chat_model, poll_interval_seconds: float, batch_size: int, max_attempts: int) -> None:
    session_factory = get_session_factory()
    while True:
        try:
            await run_memory_outbox_once(
                session_factory=session_factory,
                chat_model=chat_model,
                batch_size=batch_size,
                max_attempts=max_attempts,
            )
            await expire_due_memories_once(session_factory=session_factory)
        except Exception:
            logger.exception("memory outbox loop iteration failed")
        await asyncio.sleep(poll_interval_seconds)
