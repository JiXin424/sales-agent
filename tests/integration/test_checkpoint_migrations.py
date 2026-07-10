"""Integration smoke test for LangGraph checkpoint migrations.

Validates that ALL checkpoint migrations — including CREATE INDEX
CONCURRENTLY — can be applied against a real PostgreSQL database.
If this test fails in CI, the deploy must be blocked.

This prevents the class of regression where a LangGraph upgrade
introduces migrations that cannot execute in production (e.g.
CONCURRENTLY inside a transaction block).

Run with:
    TEST_DATABASE_URL=postgresql+asyncpg://... \\
    pytest tests/integration/test_checkpoint_migrations.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from sales_agent.graph.checkpoint_runtime import (
    initialize_production_checkpointer,
    close_production_checkpointer,
    production_checkpoint_ready,
)

# Use the same default as other integration tests (Docker network PG).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@172.26.0.2:5432/sales_agent_test",
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_checkpoint_migrations_apply_and_are_idempotent():
    """All migrations must apply cleanly and be re-runnable."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    # Drop checkpoint tables from previous runs so we start clean.
    await _drop_checkpoint_tables()

    # ── First run: create everything from scratch ─────────────────
    saver = await initialize_production_checkpointer(TEST_DATABASE_URL)
    assert saver is not None
    assert production_checkpoint_ready()

    # Verify core tables exist.
    rows = await _fetch_table_names(saver)
    assert "checkpoints" in rows, "checkpoints table was not created"
    assert "checkpoint_blobs" in rows, "checkpoint_blobs table was not created"
    assert "checkpoint_writes" in rows, "checkpoint_writes table was not created"
    assert "checkpoint_migrations" in rows, "checkpoint_migrations table was not created"

    # Verify the CONCURRENTLY indexes exist.
    indexes = await _fetch_index_names(saver)
    expected_indexes = {
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    }
    missing = expected_indexes - indexes
    assert not missing, (
        f"CONCURRENTLY indexes were not created: {missing}. "
        f"Found indexes: {indexes}"
    )

    # ── Tear down and re-run: must be idempotent ──────────────────
    await close_production_checkpointer()
    assert not production_checkpoint_ready()

    # Second run must succeed (tables already exist, migrations skip).
    saver2 = await initialize_production_checkpointer(TEST_DATABASE_URL)
    assert saver2 is not None
    assert production_checkpoint_ready()

    rows2 = await _fetch_table_names(saver2)
    for tbl in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert tbl in rows2, f"{tbl} missing after idempotent re-run"

    indexes2 = await _fetch_index_names(saver2)
    still_missing = expected_indexes - indexes2
    assert not still_missing, (
        f"Indexes missing after idempotent re-run: {still_missing}"
    )

    await close_production_checkpointer()

    # Clean up so repeated test runs start fresh.
    await _drop_checkpoint_tables()


# ── helpers ────────────────────────────────────────────────────────


def _pg_dsn(db_url: str) -> str:
    """Convert asyncpg URL to a psycopg/libpq DSN."""
    return db_url.replace("+asyncpg", "")


async def _drop_checkpoint_tables() -> None:
    """Drop all LangGraph checkpoint tables so we start fresh."""
    conn = await asyncpg.connect(_pg_dsn(TEST_DATABASE_URL))
    try:
        await conn.execute(
            "DROP TABLE IF EXISTS "
            "checkpoint_writes, checkpoint_blobs, checkpoints, "
            "checkpoint_migrations CASCADE"
        )
    finally:
        await conn.close()


async def _fetch_table_names(saver) -> set[str]:
    """Fetch public table names relevant to checkpoint."""
    async with saver.conn.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE 'checkpoint%'"
            )
            return {row[0] for row in await cur.fetchall()}


async def _fetch_index_names(saver) -> set[str]:
    """Fetch index names on checkpoint tables."""
    async with saver.conn.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT indexname FROM pg_catalog.pg_indexes "
                "WHERE schemaname = 'public' AND tablename LIKE 'checkpoint%'"
            )
            return {row[0] for row in await cur.fetchall()}
