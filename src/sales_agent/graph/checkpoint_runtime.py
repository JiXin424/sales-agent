"""Strict PostgreSQL checkpoint runtime with fail-closed semantics."""

from __future__ import annotations

import logging

from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)


class CheckpointUnavailableError(RuntimeError):
    """Raised when production checkpoint runtime is not ready."""
    pass


_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


async def initialize_production_checkpointer(
    database_url: str | None = None,
) -> AsyncPostgresSaver:
    """Initialize the production PostgreSQL checkpointer.

    Runs LangGraph checkpoint migrations with autocommit so that
    ``CREATE INDEX CONCURRENTLY`` statements are not rejected by
    PostgreSQL (CONCURRENTLY cannot run inside a transaction block).

    Args:
        database_url: Optional override for the database URL. If not provided,
            uses settings.database.url.

    Returns:
        The initialized AsyncPostgresSaver.

    Raises:
        CheckpointUnavailableError: If initialization fails.
    """
    global _pool, _saver
    if _saver is not None:
        return _saver

    url = database_url or get_settings().database.url
    conninfo = url.replace("+asyncpg", "")
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=5,
        open=False,
    )
    try:
        await pool.open()
        saver = AsyncPostgresSaver(conn=pool)

        # Run migrations **outside of a transaction block**.
        # LangGraph's built-in setup() wraps everything in a
        # transaction, but MIGRATIONS 6-8 use CREATE INDEX
        # CONCURRENTLY which PostgreSQL forbids inside a
        # transaction.  We execute the same migrations here
        # with autocommit enabled so they always succeed.
        await _run_migrations(pool, saver)
    except Exception as exc:
        await pool.close()
        raise CheckpointUnavailableError(
            "PostgreSQL checkpoint initialization failed"
        ) from exc

    _pool = pool
    _saver = saver
    return saver


async def _run_migrations(
    pool: AsyncConnectionPool,
    saver: AsyncPostgresSaver,
) -> None:
    """Run LangGraph checkpoint migrations with autocommit.

    psycopg connections manage transactions automatically (BEGIN on
    first statement, COMMIT / ROLLBACK on context exit).  We enable
    ``autocommit`` so that each statement runs in its own implicit
    transaction — this lets ``CREATE INDEX CONCURRENTLY`` succeed.
    """
    migrations = list(AsyncPostgresSaver.MIGRATIONS)
    if not migrations:
        logger.info("No checkpoint migrations to run")
        return

    async with pool.connection() as conn:
        await conn.set_autocommit(True)
        async with conn.cursor() as cur:
            # Create the migration-tracking table first (MIGRATIONS[0]).
            await cur.execute(migrations[0])

            # Check current version.
            await cur.execute(
                "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
            )
            row = await cur.fetchone()
            version = -1 if row is None else row["v"]

            for v, migration in enumerate(migrations):
                if v <= version:
                    continue
                logger.info(
                    "Running checkpoint migration %d/%d",
                    v,
                    len(migrations) - 1,
                )
                await cur.execute(migration)
                await cur.execute(
                    "INSERT INTO checkpoint_migrations (v) VALUES (%s) "
                    "ON CONFLICT (v) DO NOTHING",
                    (v,),
                )

    logger.info(
        "Checkpoint migrations complete (version %d → %d)",
        version,
        len(migrations) - 1,
    )

    # Sync pipe if the saver has one (newer langgraph versions).
    if getattr(saver, "pipe", None):
        await saver.pipe.sync()


def get_production_checkpointer() -> AsyncPostgresSaver:
    """Get the production checkpointer.

    Returns:
        The initialized AsyncPostgresSaver.

    Raises:
        CheckpointUnavailableError: If the runtime has not been initialized.
    """
    if _saver is None:
        raise CheckpointUnavailableError(
            "Production checkpoint runtime is not initialized"
        )
    return _saver


async def close_production_checkpointer() -> None:
    """Close the production checkpointer and clear cached state."""
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None


def production_checkpoint_ready() -> bool:
    """Check if the production checkpoint runtime is ready.

    Returns:
        True if the checkpointer is initialized and ready.
    """
    return _saver is not None
