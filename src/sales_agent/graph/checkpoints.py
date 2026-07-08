"""Checkpointer factory for LangGraph graphs.

Provides:
- ``get_checkpointer_sync()`` - InMemorySaver singleton for unit tests
- ``get_checkpointer()`` - Strict AsyncPostgresSaver for production (await required)
- ``initialize_production_checkpointer()`` - Initialize PostgreSQL runtime
- ``get_production_checkpointer()`` - Get production checkpointer
- ``close_production_checkpointer()`` - Shutdown production runtime
- ``production_checkpoint_ready()`` - Check if production runtime is ready
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from sales_agent.graph.checkpoint_runtime import (
    CheckpointUnavailableError,
    initialize_production_checkpointer,
    get_production_checkpointer,
    close_production_checkpointer,
    production_checkpoint_ready,
)

_in_memory_saver: InMemorySaver | None = None


def get_checkpointer_sync() -> InMemorySaver:
    """Return a shared InMemorySaver for unit tests and local dev."""
    global _in_memory_saver
    if _in_memory_saver is None:
        _in_memory_saver = InMemorySaver()
    return _in_memory_saver


async def get_checkpointer() -> AsyncPostgresSaver:
    """Return the production checkpointer (strict).

    Requires that ``initialize_production_checkpointer()`` has been called first.
    Raises CheckpointUnavailableError if the runtime is not ready.
    """
    return get_production_checkpointer()


__all__ = [
    "CheckpointUnavailableError",
    "get_checkpointer",
    "get_checkpointer_sync",
    "initialize_production_checkpointer",
    "get_production_checkpointer",
    "close_production_checkpointer",
    "production_checkpoint_ready",
]
