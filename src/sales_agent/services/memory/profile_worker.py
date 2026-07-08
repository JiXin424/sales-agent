from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sales_agent.core.database import get_session_factory
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def compute_profile_backoff_seconds(attempts: int) -> int:
    return min(300, 2 ** max(1, attempts))


async def run_profile_rebuild_once(*, session_factory, batch_size: int, max_attempts: int) -> int:
    async with session_factory() as db:
        repo = UserMemoryProfileRepository(db)
        jobs = await repo.list_pending_rebuild_jobs(limit=batch_size)
        processed = 0
        for job in jobs:
            try:
                scope = MemoryScope(tenant_id=job.tenant_id, agent_id=job.agent_id, user_id=job.user_id)
                await repo.rebuild_profile_for_scope(scope)
                job.status = "done"
                job.last_error = None
                processed += 1
            except Exception as exc:
                job.attempts += 1
                job.last_error = str(exc)[:1000]
                if job.attempts >= max_attempts:
                    job.status = "dead"
                else:
                    job.status = "pending"
                    job.available_at = utc_now() + timedelta(seconds=compute_profile_backoff_seconds(job.attempts))
        await db.commit()
        return processed


async def reconcile_stale_profiles_once(*, session_factory) -> int:
    async with session_factory() as db:
        repo = UserMemoryProfileRepository(db)
        count = await repo.enqueue_stale_profile_rebuilds(limit=100)
        await db.commit()
        return count


async def profile_rebuild_loop(*, poll_interval_seconds: float, batch_size: int, max_attempts: int) -> None:
    session_factory = get_session_factory()
    while True:
        try:
            await run_profile_rebuild_once(
                session_factory=session_factory,
                batch_size=batch_size,
                max_attempts=max_attempts,
            )
            await reconcile_stale_profiles_once(session_factory=session_factory)
        except Exception:
            logger.exception("profile rebuild loop failed")
        await asyncio.sleep(poll_interval_seconds)
