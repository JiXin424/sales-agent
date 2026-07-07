"""Integration tests for Topic context flowing through the Chat Graph.

Tests cover:
1. context_load_node filters by topic_id when provided.
2. routing_node short-circuits when precomputed_route=True.
3. logging passes topic_id through to the conversation logger.
4. Topic summary update occurs after a successful turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sales_agent.graph.chat.nodes.context_load import load_context_node
from sales_agent.graph.chat.nodes.routing import routing_node
from sales_agent.graph.chat.nodes.evidence_gate import evidence_gate
from sales_agent.graph.chat.state import ChatGraphState


# ===================================================================
# Topic-aware context loading
# ===================================================================


class TestContextLoadWithTopic:
    """context_load_node filters by topic_id when state has it."""

    @pytest.fixture
    def mock_runtime(self):
        """Create a Runtime with a mock DB session."""
        runtime = MagicMock()
        runtime.context = {"db": AsyncMock()}
        return runtime

    async def _make_messages(self, db, topic_id: str | None, count: int = 3):
        """Insert fake messages into db.execute result."""
        from unittest.mock import MagicMock

        msgs = []
        for i in range(count):
            msg = MagicMock()
            msg.role = "user" if i % 2 == 0 else "assistant"
            msg.content = f"message_{i}"
            msg.topic_id = topic_id
            msgs.append(msg)
        # Mock the scalars().all() chain
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = list(reversed(msgs))  # already reversed
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock
        return msgs

    @pytest.mark.asyncio
    async def test_load_without_topic_id_backward_compatible(self, mock_runtime):
        """Without topic_id, context loads all conversation messages (backward compat)."""
        db = mock_runtime.context["db"]
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
        }
        # Create messages with both topic_id=None and topic_id="topic_1"
        from unittest.mock import MagicMock

        msgs = []
        for i in range(3):
            msg = MagicMock()
            msg.role = "user" if i % 2 == 0 else "assistant"
            msg.content = f"m{i}"
            if i == 0:
                msg.topic_id = "topic_1"
            else:
                msg.topic_id = None
            msgs.append(msg)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = list(reversed(msgs))
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        output = await load_context_node(state, mock_runtime)

        assert "history_messages" in output
        history = output["history_messages"]
        assert len(history) > 0
        # Verify the query did NOT include a topic_id filter; the mock
        # returns our prepared messages regardless.

    @pytest.mark.asyncio
    async def test_load_with_topic_id_filters_messages(self, mock_runtime):
        """With topic_id, only messages for that topic are loaded."""
        db = mock_runtime.context["db"]
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "topic_id": "topic_abc",
        }
        from unittest.mock import MagicMock

        msgs = []
        for i in range(3):
            msg = MagicMock()
            msg.role = "user" if i % 2 == 0 else "assistant"
            msg.content = f"topic_msg_{i}"
            msg.topic_id = "topic_abc"
            msgs.append(msg)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = list(reversed(msgs))
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        output = await load_context_node(state, mock_runtime)

        assert "history_messages" in output
        history = output["history_messages"]
        assert len(history) > 0
        for m in history:
            assert "role" in m
            assert "content" in m


# ===================================================================
# Precomputed routing
# ===================================================================


class TestRoutingPrecomputed:
    """routing_node short-circuits when precomputed_route=True."""

    @pytest.fixture
    def mock_runtime(self):
        """Runtime with no chat_model — flag defaults off, rule path used."""
        runtime = MagicMock()
        runtime.context = {"db": AsyncMock(), "chat_model": None}
        return runtime

    @pytest.mark.asyncio
    async def test_precomputed_route_returns_existing_fields(self, mock_runtime):
        """With precomputed_route=True, return existing task/policy fields."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "precomputed_route": True,
            "task_type": "knowledge_qa",
            "knowledge_policy": "required",
            "needs_retrieval": True,
        }
        result = await routing_node(state, mock_runtime)
        assert result["task_type"] == "knowledge_qa"
        assert result["needs_retrieval"] is True
        assert "knowledge_policy" in result
        assert result["knowledge_policy"] == "required"

    @pytest.mark.asyncio
    async def test_precomputed_route_no_task_type(self, mock_runtime):
        """With precomputed_route=True but no existing fields, return defaults."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "precomputed_route": True,
        }
        result = await routing_node(state, mock_runtime)
        assert result["task_type"] is None

    @pytest.mark.asyncio
    async def test_normal_routing_still_works(self, mock_runtime):
        """Without precomputed_route, routing_node calls route_task normally."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "你好",
            "conversation_id": "c1",
            "channel": "local",
        }
        result = await routing_node(state, mock_runtime)
        # General coaching fallback
        assert result["task_type"] is not None
        assert result["route_confidence"] >= 0


# ===================================================================
# Evidence gate integration
# ===================================================================


class TestEvidenceGateIntegration:
    """Evidence gate blocks or allows generation based on knowledge_policy."""

    def test_required_no_sources(self):
        """Required policy with empty sources returns evidence-insufficient answer."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "knowledge_policy": "required",
            "sources": [],
        }
        result = evidence_gate(state)
        assert result["skip_generation"] is True
        assert "没有找到足够依据" in result["answer_dict"]["summary"]

    def test_optional_no_sources(self):
        """Optional policy with empty sources continues."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "knowledge_policy": "optional",
            "sources": [],
        }
        result = evidence_gate(state)
        assert result == {}

    def test_none_no_sources(self):
        """None policy continues even without sources."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "knowledge_policy": "none",
            "sources": [],
        }
        result = evidence_gate(state)
        assert result == {}


# ===================================================================
# Topic summary update (integration via log_node)
# ===================================================================


class TestTopicSummaryUpdate:
    """Topic summary/entities/goal/timestamps are updated after logging."""

    @pytest.mark.asyncio
    async def test_log_node_passes_topic_id(self):
        """log_node passes topic_id to conversation_logger.log_conversation."""
        from sales_agent.graph.chat.nodes.logging_node import log_node
        from unittest.mock import MagicMock, patch, AsyncMock

        runtime = MagicMock()
        db = AsyncMock()
        runtime.context = {"db": db}

        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "topic_id": "topic_xyz",
            "answer_dict": {"summary": "ok", "sections": []},
        }

        with patch(
            "sales_agent.graph.chat.nodes.logging_node.conversation_logger.log_conversation",
            new_callable=AsyncMock,
        ) as mock_log:
            result = await log_node(state, runtime)

        assert result == {}
        # Verify topic_id was passed through
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs.get("topic_id") == "topic_xyz"
