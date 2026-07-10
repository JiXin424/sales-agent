"""PostgreSQL advisory lock for serializing same-thread online turns.

Production turns for the SAME thread (``online:<tenant>:<agent>:<channel>:<user>``)
can be delivered concurrently by different worker processes (e.g. the
DingTalk Stream client and the HTTP worker). Without serialization, two
workers can read the same checkpoint state and both invoke Chat / advance
a guided flow, producing duplicate replies and lost updates.

This module provides a transaction-scoped PostgreSQL advisory lock keyed
on the stable thread ID, so the second worker blocks until the first
worker's transaction commits — guaranteeing it sees the first worker's
latest checkpoint state.

Contract
--------

* The lock is **transaction-scoped** (``pg_advisory_xact_lock``): it is
  held on the caller's session transaction and released automatically
  when that transaction commits or rolls back.
* The caller **owns the transaction lifecycle**.  This module acquires
  the lock but never commits or rolls back — the caller MUST do so after
  the Graph invocation and persistence complete.
* The lock key is a stable signed 64-bit integer (PostgreSQL bigint)
  derived from an 8-byte BLAKE2b digest of the full thread ID, so the
  same thread always maps to the same key across processes and restarts.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def online_turn_lock_key(thread_id: str) -> int:
    """Stable signed PostgreSQL bigint lock key for a thread ID.

    Uses an 8-byte BLAKE2b digest of the full thread ID, interpreted as a
    signed 64-bit integer in the PostgreSQL bigint range ``[-2**63, 2**63)``.
    The same thread ID always yields the same key, across processes and
    restarts, so all workers contend on the same lock for a given thread.
    """
    digest = hashlib.blake2b(thread_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def acquire_online_turn_lock(db: AsyncSession, thread_id: str) -> None:
    """Acquire a transaction-scoped advisory lock for the thread.

    Uses ``pg_advisory_xact_lock`` so the lock is held until the caller's
    transaction commits or rolls back. The caller owns the transaction
    and MUST commit/rollback after the Graph invocation and persistence
    complete; this function does not manage the transaction lifecycle.

    Args:
        db: Async SQLAlchemy session. Its transaction is used to scope the
            lock — committing or rolling back this session releases it.
        thread_id: Stable thread ID string (e.g.
            ``"online:<tenant>:<agent>:<channel>:<user>"``).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": online_turn_lock_key(thread_id)},
    )
