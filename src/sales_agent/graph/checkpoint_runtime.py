"""Strict PostgreSQL checkpoint runtime with fail-closed semantics."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sales_agent.core.config import get_settings


class CheckpointUnavailableError(RuntimeError):
    """Raised when production checkpoint runtime is not ready."""
    pass


_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


async def initialize_production_checkpointer(
    database_url: str | None = None,
) -> AsyncPostgresSaver:
    """Initialize the production PostgreSQL checkpointer.

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
        await saver.setup()
    except Exception as exc:
        await pool.close()
        raise CheckpointUnavailableError(
            "PostgreSQL checkpoint initialization failed"
        ) from exc

    _pool = pool
    _saver = saver
    return saver


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
