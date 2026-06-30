"""Checkpointer factory for LangGraph graphs.

Provides:
- `get_checkpointer_sync()` — InMemorySaver singleton for unit tests
- `get_checkpointer()` — AsyncPostgresSaver for production (await required)
"""

from __future__ import annotations

import logging
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)

_in_memory_saver: InMemorySaver | None = None
_async_pg_saver: AsyncPostgresSaver | None = None


def get_checkpointer_sync() -> InMemorySaver:
    """Return a shared InMemorySaver for unit tests and local dev."""
    global _in_memory_saver
    if _in_memory_saver is None:
        _in_memory_saver = InMemorySaver()
    return _in_memory_saver


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
        # "postgresql+asyncpg://user:pass@host:port/db" -> "postgresql://user:pass@host:port/db"
        conn_string = db_url.replace("+asyncpg", "")

        # Use AsyncConnectionPool for a persistent, production-grade saver.
        # AsyncConnectionPool manages connection lifecycle and is compatible
        # with AsyncPostgresSaver's expected Conn type.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(conn_string, min_size=1, max_size=5, open=True)
        _async_pg_saver = AsyncPostgresSaver(conn=pool)
        await _async_pg_saver.setup()
        logger.info("AsyncPostgresSaver initialized with PostgreSQL pool")
        return _async_pg_saver

    except Exception as e:
        logger.warning(
            "Failed to create AsyncPostgresSaver (%s), falling back to InMemorySaver", e
        )
        return get_checkpointer_sync()
