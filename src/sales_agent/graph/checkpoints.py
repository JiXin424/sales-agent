"""Checkpointer factory for LangGraph graphs.

Provides:
- ``get_checkpointer_sync()`` — InMemorySaver singleton for unit tests
- ``get_checkpointer()`` — AsyncPostgresSaver for production (await required)
"""

from __future__ import annotations

import logging
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)

_in_memory_saver: InMemorySaver | None = None
_async_pg_saver: AsyncPostgresSaver | None = None

# ── Online graph checkpointer (process-level singleton) ───────────────
_online_in_memory_saver: InMemorySaver | None = None


def get_checkpointer_sync() -> InMemorySaver:
    """Return a shared InMemorySaver for unit tests and local dev."""
    global _in_memory_saver
    if _in_memory_saver is None:
        _in_memory_saver = InMemorySaver()
    return _in_memory_saver


def get_online_checkpointer_sync() -> InMemorySaver:
    """Return a process-level InMemorySaver for the Online Graph.

    This checkpointer is shared across all production conversation turns
    and **must not** be created per request.  Tests should pass their
    own :class:`InMemorySaver` to ``build_online_graph().compile()``
    instead of using this singleton.
    """
    global _online_in_memory_saver
    if _online_in_memory_saver is None:
        _online_in_memory_saver = InMemorySaver()
    return _online_in_memory_saver


async def get_checkpointer() -> AsyncPostgresSaver | InMemorySaver:
    """Return the production checkpointer.

    If DATABASE_URL is set and asyncpg is available, returns AsyncPostgresSaver.
    Otherwise falls back to InMemorySaver.

    The first call runs ``setup()`` to create checkpoint tables.
    """
    global _async_pg_saver, _in_memory_saver

    if _async_pg_saver is not None:
        return _async_pg_saver

    from sales_agent.core.config import get_settings

    try:
        settings = get_settings()
        db_url = settings.database.url

        # Convert sqlalchemy URL to psycopg connection string
        conn_string = db_url.replace("+asyncpg", "")

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(conn_string, min_size=1, max_size=5, open=True)
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()
        _async_pg_saver = saver  # only cache after successful setup
        logger.info("AsyncPostgresSaver initialized with PostgreSQL pool")
        return _async_pg_saver

    except Exception as e:
        logger.warning(
            "Failed to create AsyncPostgresSaver (%s), falling back to InMemorySaver", e
        )
        return get_checkpointer_sync()
