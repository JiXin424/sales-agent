"""Integration test: PostgreSQL checkpoint persistence across simulated restarts."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import asyncpg

import sales_agent.graph.checkpoint_runtime as checkpoint_runtime
from sales_agent.services.online_conversation import (
    initialize_online_runtime,
    close_online_runtime,
    get_online_graph,
    _online_graph,
)

# Use the test database URL as specified
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@172.26.0.2:5432/sales_agent_test"
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_online_checkpoint_persistence_across_restarts():
    """Test that Online Graph state persists across simulated runtime restarts."""
    # Skip if test DB not available
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    # 0. Setup: unique thread ID and test data
    unique_suffix = uuid.uuid4().hex[:8]
    test_thread_id = f"test_thread_{unique_suffix}"
    config = {"configurable": {"thread_id": test_thread_id}}

    base_input = {
        "tenant_id": "test_tenant",
        "agent_id": "test_agent",
        "user_id": "test_user",
        "session_user_id": "test_session_user",
        "channel": "test_channel",
        "conversation_id": "test_conv",
        "guided_flows_enabled": True,
        "topic_routing_enabled": True,
    }
    context = {"db": None, "chat_model": None, "embedding_model": None}

    # Point the checkpoint runtime at the isolated test DB deterministically.
    # We patch get_settings inside checkpoint_runtime (not the global env / the
    # cached _settings singleton, which earlier tests may already have populated
    # with a different URL) so the saver always connects to TEST_DATABASE_URL.
    fake_settings = SimpleNamespace(database=SimpleNamespace(url=TEST_DATABASE_URL))

    # Ensure a clean runtime regardless of prior tests.
    await close_online_runtime()

    try:
        with patch.object(checkpoint_runtime, "get_settings", return_value=fake_settings):
            # 1. Initialize runtime and execute first Guided Flow turn
            await initialize_online_runtime()
            graph_a = get_online_graph()

            # Execute first turn (start small_win_appreciation)
            result_a1 = await graph_a.ainvoke(
                {**base_input, "message": "小赢欣赏", "event_id": f"ev1_{unique_suffix}"},
                config=config,
                context=context,
            )

            assert result_a1["active_flow"] == "small_win_appreciation"
            assert result_a1["flow_stage"] == "small_win"

            # 2. Close the runtime
            await close_online_runtime()
            # Ensure the cache is cleared
            assert _online_graph is None

            # 3. Re-initialize runtime (creates new saver/pool and compiles new graph)
            await initialize_online_runtime()
            graph_b = get_online_graph()
            assert graph_b is not graph_a  # Different compiled graph instance

            # 4. Execute second turn with same thread ID
            result_b1 = await graph_b.ainvoke(
                {**base_input, "message": "今天主动联系了一个一直没回复的客户", "event_id": f"ev2_{unique_suffix}"},
                config=config,
                context=context,
            )

            # Verify B sees the prior state and advances the flow
            assert result_b1["active_flow"] == "small_win_appreciation"
            assert result_b1["flow_stage"] == "strength"

    finally:
        # 5. Clean up: delete only our test checkpoints
        # Parse TEST_DATABASE_URL to get connection params
        import re
        match = re.match(
            r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)",
            TEST_DATABASE_URL
        )
        if match:
            user, password, host, port, dbname = match.groups()
            conn = await asyncpg.connect(
                user=user,
                password=password,
                host=host,
                port=int(port),
                database=dbname,
            )
            try:
                # Delete from all three checkpoint tables for our thread_id
                for table in ["checkpoints", "checkpoint_writes", "checkpoint_blobs"]:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = $1",
                        test_thread_id
                    )
            finally:
                await conn.close()
        # Clean up runtime
        await close_online_runtime()
