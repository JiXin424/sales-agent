"""Integration test: cross-worker serialization of same-thread online turns.

Proves the PostgreSQL transaction-scoped advisory lock
(``pg_advisory_xact_lock``) serializes concurrent turns for the SAME
thread, while allowing DIFFERENT threads (different users) to proceed in
parallel. Also verifies that delivering the same ``event_id`` a second
time — after the first turn committed — is detected as a duplicate
(``response_kind == "duplicate"``) and does not invoke Chat again.

Design
------

* Each turn runs in its own :class:`AsyncSession` (independent connection)
  so the advisory lock is genuinely contended across PostgreSQL
  connections — mirroring two separate worker processes.
* The Chat node is replaced by a ``_ConcurrencyChatRunner`` stub injected
  via the runtime context (``chat_runner``).  The stub tracks how many
  invocations are in-flight simultaneously; ``max_in_flight`` therefore
  reflects true concurrency at the Chat node.
* The Online Graph is compiled once against the test PostgreSQL
  checkpointer (``initialize_online_runtime``) and shared by both turns
  via the module-level singleton, so checkpoint state written by turn A
  is visible to turn B once A's transaction commits.
* Checkpoint rows created during a run are deleted by ``thread_id``.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import sales_agent.graph.checkpoint_runtime as checkpoint_runtime
from sales_agent.services.online_conversation import (
    build_online_turn_input,
    close_online_runtime,
    get_online_graph,
    initialize_online_runtime,
)
from sales_agent.services.online_turn_lock import acquire_online_turn_lock

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@172.26.0.2:5432/sales_agent_test",
)


# ====================================================================
# Helpers
# ====================================================================


class _ConcurrencyChatRunner:
    """Stub Chat runner that tracks concurrent in-flight invocations."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.call_count = 0

    async def ainvoke(self, input_dict, config=None, context=None, **kwargs):
        self.call_count += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Widen the concurrency window so that unsynchronized turns
            # would overlap and bump max_in_flight above 1.
            await asyncio.sleep(0.1)
            return {
                "answer_dict": {"summary": "stub reply", "sections": []},
                "final_answer": {"summary": "stub reply", "sections": []},
            }
        finally:
            self.in_flight -= 1


async def _run_turn(engine, thread_id: str, event_id: str, runner):
    """Run a single serialized turn: acquire lock → invoke graph → commit.

    Each turn uses its own AsyncSession (independent connection), matching
    the production model where each worker holds its own DB session and
    transaction.  The caller owns the transaction; committing releases the
    transaction-scoped advisory lock.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Acquire the transaction-scoped advisory lock BEFORE the Graph
        # invocation so the second worker cannot enter Chat until the
        # first worker commits.
        await acquire_online_turn_lock(session, thread_id)
        graph = get_online_graph()
        session_user_id = thread_id.rsplit(":", 1)[-1]
        input_state = build_online_turn_input(
            tenant_id="t_conc",
            agent_id="a_conc",
            user_id="u_conc",
            session_user_id=session_user_id,
            channel="dingtalk",
            conversation_id="c_conc",
            message="hello world",
            entry_action=None,
            event_id=event_id,
            guided_flows_enabled=False,
            topic_routing_enabled=False,
        )
        result = await graph.ainvoke(
            input_state,
            config={"configurable": {"thread_id": thread_id}},
            context={
                "db": session,
                "chat_model": None,
                "chat_runner": runner,
            },
        )
        # Committing the session releases pg_advisory_xact_lock.
        await session.commit()
        return result


async def _cleanup_checkpoints(thread_ids: list[str]) -> None:
    """Delete checkpoint rows for the given thread IDs."""
    match = re.match(
        r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)",
        TEST_DATABASE_URL,
    )
    if not match:
        return
    user, password, host, port, dbname = match.groups()
    conn = await asyncpg.connect(
        user=user,
        password=password,
        host=host,
        port=int(port),
        database=dbname,
    )
    try:
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
            await conn.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY($1)",
                thread_ids,
            )
    finally:
        await conn.close()


async def _cleanup_advisory_locks(thread_ids: list[str]) -> None:
    """Best-effort: forcibly release any lingering transaction-scoped
    advisory locks (only relevant if a test errored mid-turn)."""
    from sales_agent.services.online_turn_lock import online_turn_lock_key

    match = re.match(
        r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)",
        TEST_DATABASE_URL,
    )
    if not match:
        return
    user, password, host, port, dbname = match.groups()
    conn = await asyncpg.connect(
        user=user,
        password=password,
        host=host,
        port=int(port),
        database=dbname,
    )
    try:
        for tid in thread_ids:
            # Session-scoped unlock is a no-op for xact locks, but
            # pg_advisory_unlock_unlocked is not necessary — xact locks
            # die with the transaction. Nothing to do here; kept for
            # future session-scoped migration.
            _ = online_turn_lock_key(tid)
    finally:
        await conn.close()


# ====================================================================
# Fixtures
# ====================================================================


@pytest.fixture
async def pg_runtime():
    """Initialize the Online Graph runtime against the test PostgreSQL DB.

    Ensures a clean runtime regardless of prior tests and tears it down
    afterwards so the module-level graph cache does not leak.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    fake_settings = SimpleNamespace(database=SimpleNamespace(url=TEST_DATABASE_URL))
    await close_online_runtime()
    with patch.object(checkpoint_runtime, "get_settings", return_value=fake_settings):
        await initialize_online_runtime()
        try:
            yield
        finally:
            await close_online_runtime()


# ====================================================================
# Tests
# ====================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_thread_turns_serialize(pg_runtime):
    """Two turns for the SAME thread must NOT run Chat concurrently.

    The second worker blocks on ``pg_advisory_xact_lock`` until the first
    worker's transaction commits, so Chat is entered at most once at a
    time → ``max_in_flight == 1``.
    """
    suffix = uuid.uuid4().hex[:8]
    thread_id = f"online:t_conc:a_conc:dingtalk:same-{suffix}"
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=5)
    runner = _ConcurrencyChatRunner()
    try:
        await asyncio.gather(
            _run_turn(engine, thread_id, f"ev-a-{suffix}", runner),
            _run_turn(engine, thread_id, f"ev-b-{suffix}", runner),
        )
    finally:
        await engine.dispose()

    assert runner.max_in_flight == 1
    assert runner.call_count == 2
    await _cleanup_checkpoints([thread_id])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_different_thread_turns_run_concurrently(pg_runtime):
    """Two turns for DIFFERENT threads (different users) run in parallel.

    The advisory lock keys are distinct, so neither worker blocks the
    other and both enter Chat at the same time → ``max_in_flight == 2``.
    """
    suffix = uuid.uuid4().hex[:8]
    thread_a = f"online:t_conc:a_conc:dingtalk:userA-{suffix}"
    thread_b = f"online:t_conc:a_conc:dingtalk:userB-{suffix}"
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=5)
    runner = _ConcurrencyChatRunner()
    try:
        await asyncio.gather(
            _run_turn(engine, thread_a, f"ev-a-{suffix}", runner),
            _run_turn(engine, thread_b, f"ev-b-{suffix}", runner),
        )
    finally:
        await engine.dispose()

    assert runner.max_in_flight == 2
    assert runner.call_count == 2
    await _cleanup_checkpoints([thread_a, thread_b])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_event_after_commit_is_detected(pg_runtime):
    """Delivering the same ``event_id`` twice (after the first commits)
    yields ``response_kind == "duplicate"`` and Chat runs exactly once."""
    suffix = uuid.uuid4().hex[:8]
    thread_id = f"online:t_conc:a_conc:dingtalk:dup-{suffix}"
    event_id = f"ev-dup-{suffix}"
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=5)
    runner = _ConcurrencyChatRunner()
    try:
        first = await _run_turn(engine, thread_id, event_id, runner)
        duplicate = await _run_turn(engine, thread_id, event_id, runner)
    finally:
        await engine.dispose()

    assert first["response_kind"] == "chat"
    assert duplicate["response_kind"] == "duplicate"
    assert runner.call_count == 1
    await _cleanup_checkpoints([thread_id])
