"""Tests for context routing and evidence routing nodes.

Covers:
- context_resolution_node: resolved, clarify, pending clarification handling
- evidence_routing_node: decision mapping
- Graph-level routing: clarify path vs resolved path vs guided flow bypass
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.graph.online_graph import (
    build_online_graph,
)
from sales_agent.graph.nodes.context_resolution import context_resolution_node
from sales_agent.graph.nodes.evidence_routing import evidence_routing_node
from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
    EvidenceDecision,
)


# ===================================================================
# Stubs / Helpers
# ===================================================================


class StubChatRunner:
    """Replaces the real Chat Graph so routing tests never need a real model."""

    async def ainvoke(self, input_dict, config=None, context=None, **kwargs):
        return {
            "answer_dict": {"summary": "chat stub reply", "sections": []},
            "final_answer": {"summary": "chat stub reply", "sections": []},
        }


def _make_context_decision(turn_relation: str = "continue", **kw) -> ContextDecision:
    return ContextDecision(
        turn_relation=turn_relation,
        standalone_query=kw.pop("standalone_query", "test standalone query"),
        retained_entities=kw.pop("retained_entities", []),
        retracted_goals=kw.pop("retracted_goals", []),
        missing_references=kw.pop("missing_references", []),
        confidence=kw.pop("confidence", 0.95),
        reason_code=kw.pop("reason_code", "test"),
        **kw,
    )


def _make_evidence_decision(intent: str = "general_sales_coaching", **kw) -> EvidenceDecision:
    return EvidenceDecision(
        intent=intent,
        response_mode=kw.pop("response_mode", "direct"),
        knowledge_policy=kw.pop("knowledge_policy", "none"),
        knowledge_scope=kw.pop("knowledge_scope", []),
        retrieval_query=kw.pop("retrieval_query", None),
        confidence=kw.pop("confidence", 0.9),
        reason_code=kw.pop("reason_code", "test"),
        **kw,
    )


def _build_config(context: dict) -> dict:
    """Build a RunnableConfig-compatible dict with the given runtime context."""
    return {
        "configurable": {
            "__pregel_runtime": SimpleNamespace(context=context),
        },
    }


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_topic():
    """Create a minimal mock ConversationTopic."""
    topic = MagicMock(spec=ConversationTopic)
    topic.id = str(uuid.uuid4())
    topic.tenant_id = "t1"
    topic.agent_id = "a1"
    topic.user_id = "u1"
    topic.channel = "dingtalk"
    topic.status = "active"
    topic.summary = "Test topic summary"
    topic.current_goal = "Test goal"
    topic.key_entities_json = "[]"
    topic.pending_clarification_json = None
    topic.clarification_attempts = 0
    topic.last_active_at = datetime.now(timezone.utc)
    return topic


@pytest.fixture
def mock_topic_manager(mock_topic):
    """Create a mock TopicManager with controllable behavior."""
    mgr = MagicMock()
    mgr.get_active_topic = AsyncMock(return_value=mock_topic)
    mgr.close_if_expired = AsyncMock(return_value=False)
    mgr.find_restorable_topics = AsyncMock(return_value=[])
    mgr.create_topic = AsyncMock(return_value=mock_topic)
    mgr.set_pending_clarification = AsyncMock(side_effect=lambda db, t, e, m, c: t)
    mgr.cancel_pending = AsyncMock(side_effect=lambda db, t: t)
    mgr.resolve_pending = AsyncMock(side_effect=lambda db, t, d, now=None: t)
    mgr.apply_context_decision = AsyncMock(side_effect=lambda db, t, d, now=None: t)
    return mgr


@pytest.fixture
def online_graph():
    """Return a compiled online graph with a fresh InMemorySaver."""
    return build_online_graph().compile(checkpointer=InMemorySaver())


@pytest.fixture
def config():
    """Return a unique thread config for each test."""
    return {"configurable": {"thread_id": f"test:{uuid.uuid4().hex[:12]}"}}


# ===================================================================
# context_resolution_node unit tests
# ===================================================================


@pytest.mark.asyncio
async def test_context_resolution_resolved(mock_topic, mock_topic_manager):
    """Node returns resolved state with standalone_query when context is clear."""
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="continue",
                standalone_query="什么样的客户适合做访前准备",
            ),
        ),
        "clarification_resolver_override": AsyncMock(),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "什么样的客户适合做访前准备",
            "event_id": "ev-1",
        },
        cfg,
    )
    assert result["context_status"] == "resolved"
    assert result["standalone_query"] == "什么样的客户适合做访前准备"
    assert result["turn_relation"] == "continue"
    assert result["original_message"] == "什么样的客户适合做访前准备"
    assert result.get("response_kind") != "clarify"


@pytest.mark.asyncio
async def test_context_resolution_clarify(mock_topic, mock_topic_manager):
    """Node returns clarify state with answer when context is ambiguous."""
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="ambiguous",
                standalone_query="",
            ),
        ),
        "clarification_resolver_override": AsyncMock(),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "你好",
            "event_id": "ev-1",
        },
        cfg,
    )
    assert result["context_status"] == "clarify"
    assert "answer_dict" in result
    assert result["response_kind"] == "clarify"
    mock_topic_manager.set_pending_clarification.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_resolution_no_topic(mock_topic_manager):
    """Node handles missing active topic gracefully."""
    mock_topic_manager.get_active_topic = AsyncMock(return_value=None)
    mock_topic_manager.find_restorable_topics = AsyncMock(return_value=[])
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="continue",
                standalone_query="什么样的客户适合做访前准备",
            ),
        ),
        "clarification_resolver_override": AsyncMock(),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "什么样的客户适合做访前准备",
            "event_id": "ev-1",
        },
        cfg,
    )
    assert result["context_status"] == "resolved"
    assert result["standalone_query"] == "什么样的客户适合做访前准备"


@pytest.mark.asyncio
async def test_context_resolution_resolves_pending_continue(mock_topic, mock_topic_manager):
    """'continue' command resolves pending clarification and restores original message."""
    mock_topic.pending_clarification_json = json.dumps({
        "event_id": "ev-original",
        "original_message": "什么样的客户适合做访前准备",
        "candidate_query": "什么样的客户适合做访前准备",
        "created_at": "2026-07-06T09:59:00+00:00",
    })
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(),  # not called when pending
        "clarification_resolver_override": AsyncMock(
            return_value=ClarificationDecision(resolution="continue", confidence=1.0),
        ),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "继续",
            "event_id": "ev-2",
        },
        cfg,
    )
    assert result["context_status"] == "resolved"
    assert result["standalone_query"] == "什么样的客户适合做访前准备"
    assert result["original_message"] == "什么样的客户适合做访前准备"
    assert result["turn_relation"] == "continue"


@pytest.mark.asyncio
async def test_context_resolution_resolves_pending_new(mock_topic, mock_topic_manager):
    """'new problem' command resolves pending as new, reruns original without old topic."""
    mock_topic.pending_clarification_json = json.dumps({
        "event_id": "ev-original",
        "original_message": "什么样的客户适合做访前准备",
        "candidate_query": "什么样的客户适合做访前准备",
        "created_at": "2026-07-06T09:59:00+00:00",
    })
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(),
        "clarification_resolver_override": AsyncMock(
            return_value=ClarificationDecision(resolution="new", confidence=1.0),
        ),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "新问题",
            "event_id": "ev-2",
        },
        cfg,
    )
    assert result["context_status"] == "resolved"
    assert result["turn_relation"] == "new"
    # Original message should still be preserved for rerouting
    assert result["original_message"] == "什么样的客户适合做访前准备"


@pytest.mark.asyncio
async def test_context_resolution_resolves_pending_replace(mock_topic, mock_topic_manager):
    """'replace' resolution clears pending and routes replacement text."""
    mock_topic.pending_clarification_json = json.dumps({
        "event_id": "ev-original",
        "original_message": "什么样的客户适合做访前准备",
        "candidate_query": "什么样的客户适合做访前准备",
        "created_at": "2026-07-06T09:59:00+00:00",
    })
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(),
        "clarification_resolver_override": AsyncMock(
            return_value=ClarificationDecision(
                resolution="replace",
                replacement_text="新问题的完整文本",
                confidence=0.9,
            ),
        ),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "新问题的完整文本",
            "event_id": "ev-2",
        },
        cfg,
    )
    assert result["context_status"] == "resolved"
    # standalone_query should use replacement_text from the decision
    assert result["standalone_query"] == "新问题的完整文本"
    assert result["turn_relation"] == "continue"
    assert result["original_message"] == "什么样的客户适合做访前准备"
    mock_topic_manager.resolve_pending.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_resolution_pending_duplicate_event(mock_topic, mock_topic_manager):
    """Same event_id as pending's original must not resolve twice."""
    mock_topic.pending_clarification_json = json.dumps({
        "event_id": "ev-original",
        "original_message": "什么样的客户适合做访前准备",
        "candidate_query": "什么样的客户适合做访前准备",
        "created_at": "2026-07-06T09:59:00+00:00",
    })
    cfg = _build_config({
        "db": None,
        "chat_model": None,
        "now": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(),
        "clarification_resolver_override": AsyncMock(),
    })
    result = await context_resolution_node(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "channel": "dingtalk", "conversation_id": "c1",
            "message": "继续",
            "event_id": "ev-original",  # same as pending's event_id
        },
        cfg,
    )
    assert result["response_kind"] == "duplicate"
    mock_topic_manager.resolve_pending.assert_not_awaited()


# ===================================================================
# evidence_routing_node unit tests
# ===================================================================


@pytest.mark.asyncio
async def test_evidence_routing():
    """Node maps EvidenceDecision to state fields."""
    cfg = _build_config({
        "chat_model": None,
        "evidence_router_override": AsyncMock(
            return_value=_make_evidence_decision(
                intent="visit_preparation",
                response_mode="retrieve",
                knowledge_policy="required",
                knowledge_scope=["sales_playbook"],
                retrieval_query="什么样的客户适合做访前准备",
            ),
        ),
    })
    result = await evidence_routing_node(
        {
            "standalone_query": "什么样的客户适合做访前准备",
            "context_status": "resolved",
        },
        cfg,
    )
    assert result["task_type"] == "visit_preparation"
    assert result["knowledge_policy"] == "required"
    assert result["needs_retrieval"] is True
    assert result["retrieval_query"] == "什么样的客户适合做访前准备"
    assert result["route_confidence"] == 0.9


@pytest.mark.asyncio
async def test_evidence_routing_direct():
    """Node sets needs_retrieval=False when policy is 'none'."""
    cfg = _build_config({
        "chat_model": None,
        "evidence_router_override": AsyncMock(
            return_value=_make_evidence_decision(
                intent="general_sales_coaching",
                response_mode="direct",
                knowledge_policy="none",
            ),
        ),
    })
    result = await evidence_routing_node(
        {
            "standalone_query": "你好",
            "context_status": "resolved",
        },
        cfg,
    )
    assert result["knowledge_policy"] == "none"
    assert result["needs_retrieval"] is False


@pytest.mark.asyncio
async def test_evidence_routing_skipped_for_clarify():
    """Node returns empty dict when context_status is not resolved."""
    cfg = _build_config({
        "chat_model": None,
        "evidence_router_override": AsyncMock(),
    })
    result = await evidence_routing_node(
        {
            "context_status": "clarify",
            "standalone_query": "",
        },
        cfg,
    )
    assert result == {}


# ===================================================================
# Graph-level integration tests
# ===================================================================


@pytest.mark.asyncio
async def test_graph_resolved_path(online_graph, config, mock_topic_manager):
    """Normal message => resolved context => evidence => chat => chat response."""
    ctx = {
        "db": None,
        "chat_model": None,
        "chat_runner": StubChatRunner(),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="continue",
                standalone_query="什么样的客户适合做访前准备",
            ),
        ),
        "evidence_router_override": AsyncMock(
            return_value=_make_evidence_decision(
                intent="general_sales_coaching",
                response_mode="direct",
                knowledge_policy="none",
            ),
        ),
    }
    result = await online_graph.ainvoke(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "session_user_id": "u1", "channel": "dingtalk",
            "conversation_id": "c1", "message": "什么样的客户适合做访前准备",
            "event_id": str(uuid.uuid4()),
            "guided_flows_enabled": False,
            "topic_routing_enabled": True,
        },
        config=config,
        context=ctx,
    )
    assert result.get("response_kind") == "chat"
    assert result.get("context_status") == "resolved"
    assert result.get("standalone_query") is not None
    assert result.get("task_type") == "general_sales_coaching"


@pytest.mark.asyncio
async def test_graph_clarify_path(online_graph, config, mock_topic_manager):
    """Ambiguous message => clarify => clarification response => END (no chat)."""
    ctx = {
        "db": None,
        "chat_model": None,
        "chat_runner": StubChatRunner(),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="ambiguous",
                standalone_query="",
            ),
        ),
        "evidence_router_override": AsyncMock(),
    }
    result = await online_graph.ainvoke(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "session_user_id": "u1", "channel": "dingtalk",
            "conversation_id": "c1", "message": "你好",
            "event_id": str(uuid.uuid4()),
            "guided_flows_enabled": False,
            "topic_routing_enabled": True,
        },
        config=config,
        context=ctx,
    )
    assert result.get("response_kind") == "clarify"
    assert result.get("context_status") == "clarify"
    ctx["evidence_router_override"].assert_not_awaited()


@pytest.mark.asyncio
async def test_guided_flow_bypasses_context_routing(online_graph, config):
    """Guided flow trigger does NOT go through context resolution."""
    result = await online_graph.ainvoke(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "session_user_id": "u1", "channel": "dingtalk",
            "conversation_id": "c1", "message": "小赢欣赏",
            "event_id": str(uuid.uuid4()),
            "guided_flows_enabled": True,
        },
        config=config,
        context={"db": None, "chat_model": None, "chat_runner": StubChatRunner()},
    )
    assert result.get("active_flow") is not None
    assert result.get("response_kind") is not None


@pytest.mark.asyncio
async def test_pending_continue_through_graph(online_graph, config, mock_topic, mock_topic_manager):
    """Two-turn flow: ambiguous => pending, then 'continue' => resolved => chat."""
    mock_topic.pending_clarification_json = json.dumps({
        "event_id": "ev-1",
        "original_message": "什么样的客户适合做访前准备",
        "candidate_query": "什么样的客户适合做访前准备",
        "created_at": "2026-07-06T09:59:00+00:00",
    })
    ctx = {
        "db": None,
        "chat_model": None,
        "chat_runner": StubChatRunner(),
        "topic_manager": mock_topic_manager,
        "context_resolver_override": AsyncMock(
            return_value=_make_context_decision(
                turn_relation="ambiguous",
                standalone_query="",
            ),
        ),
        "clarification_resolver_override": AsyncMock(
            return_value=ClarificationDecision(resolution="continue", confidence=1.0),
        ),
        "evidence_router_override": AsyncMock(
            return_value=_make_evidence_decision(
                intent="general_sales_coaching",
                response_mode="direct",
                knowledge_policy="none",
            ),
        ),
    }
    result = await online_graph.ainvoke(
        {
            "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
            "session_user_id": "u1", "channel": "dingtalk",
            "conversation_id": "c1", "message": "继续",
            "event_id": "ev-2",
            "guided_flows_enabled": False,
            "topic_routing_enabled": True,
        },
        config=config,
        context=ctx,
    )
    assert result.get("response_kind") == "chat"
    assert result.get("context_status") == "resolved"
    mock_topic_manager.resolve_pending.assert_awaited_once()
