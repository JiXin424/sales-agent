"""Tests for the checkpointer factory."""
import pytest
from sales_agent.graph.checkpoints import get_checkpointer_sync


def test_get_checkpointer_sync_returns_in_memory():
    """get_checkpointer_sync returns InMemorySaver for testing."""
    saver = get_checkpointer_sync()
    from langgraph.checkpoint.memory import InMemorySaver
    assert isinstance(saver, InMemorySaver)


def test_get_checkpointer_sync_is_singleton():
    """Multiple calls return the same instance."""
    a = get_checkpointer_sync()
    b = get_checkpointer_sync()
    assert a is b
