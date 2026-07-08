"""Unit tests for the PostgreSQL advisory lock used to serialize online turns.

Covers:

- ``online_turn_lock_key`` — stable, deterministic, signed 64-bit bigint
  within the PostgreSQL bigint range.
- ``acquire_online_turn_lock`` — issues ``pg_advisory_xact_lock`` with the
  correct lock key, using positional ``(statement, params)`` on the session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sales_agent.services.online_turn_lock import (
    acquire_online_turn_lock,
    online_turn_lock_key,
)


def test_lock_key_is_stable_signed_bigint():
    """Same input yields the same key, and the key is a signed bigint."""
    key = online_turn_lock_key("online:t1:a1:dingtalk:u1")
    assert key == online_turn_lock_key("online:t1:a1:dingtalk:u1")
    # Signed 64-bit range — matches PostgreSQL bigint
    assert -(2**63) <= key < 2**63


def test_lock_key_is_deterministic_and_distinct():
    """Different inputs must (almost certainly) yield different keys."""
    a = online_turn_lock_key("online:t1:a1:dingtalk:u1")
    b = online_turn_lock_key("online:t1:a1:dingtalk:u2")
    assert a != b


@pytest.mark.asyncio
async def test_acquire_uses_transaction_advisory_lock():
    """Acquire issues ``pg_advisory_xact_lock`` with the right lock key."""
    db = AsyncMock()
    await acquire_online_turn_lock(db, "online:t1:a1:dingtalk:u1")
    statement, params = db.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"lock_key": online_turn_lock_key("online:t1:a1:dingtalk:u1")}
