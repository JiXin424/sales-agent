"""Tests for the Online Conversation Graph.

Covers:

- Routing priority (duplicate > start > cancel > advance > chat)
- Deduplication by ``event_id`` / ``last_event_id``
- Flow preemption (new trigger starts a different flow)
- Cancel beats advance when cancel command is sent
- ``guided_flows_enabled=False`` always routes to chat
- Thread ID format and date-bounding
- ``last_event_id`` tracking
- Fresh saver / no active flow on new thread
- Same-thread state carry-over
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.graph.online_graph import build_online_graph
from sales_agent.graph.online_state import OnlineConversationState
from sales_agent.services.online_conversation import build_online_thread_id
from sales_agent.services.structured_router_output import (
    ContextDecision,
    EvidenceDecision,
)



# ====================================================================
# Stub Chat Runner
# ====================================================================


class StubChatRunner:
    """A stub that replaces the real Chat Graph in routing tests.

    Returns a fixed ``answer_dict`` so that the ``chat_node`` always
    succeeds without a real chat model or database.
    """

    async def ainvoke(self, input_dict, config=None, context=None, **kwargs):
        return {
            "answer_dict": {"summary": "chat stub reply", "sections": []},
            "final_answer": {"summary": "chat stub reply", "sections": []},
        }


# ====================================================================
# Stub resolvers for context/evidence routing
# ====================================================================


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


# ====================================================================
# Fixtures
# ====================================================================

_BASE_INPUT = {
    "tenant_id": "t1",
    "agent_id": "a1",
    "user_id": "u1",
    "session_user_id": "u1",
    "channel": "dingtalk",
    "conversation_id": "c1",
    "guided_flows_enabled": True,
}

_CONTEXT = {
    "db": None,
    "chat_model": None,
    "chat_runner": StubChatRunner(),
    "context_resolver_override": AsyncMock(
        return_value=_make_context_decision(
            turn_relation="continue",
            standalone_query="test message",
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


def _make_input(message: str, **overrides) -> dict:
    """Build an input dict for the online graph.

    Each call gets a unique ``event_id`` unless overridden, so that
    tests do not accidentally collide on deduplication.
    """
    data = dict(_BASE_INPUT)
    data["message"] = message
    data.setdefault("event_id", str(uuid.uuid4()))
    data.update(overrides)
    return data


@pytest.fixture
def online_graph():
    """Return a compiled online graph with a fresh ``InMemorySaver``."""
    return build_online_graph().compile(checkpointer=InMemorySaver())


@pytest.fixture
def config(request):
    """Return a thread-specific config dict.

    Each test gets its own thread ID by default, avoiding cross-test
    state contamination.  A test can override by marking with
    ``@pytest.mark.thread_id("custom-id")``.
    """
    marker = request.node.get_closest_marker("thread_id")
    tid = marker.args[0] if marker else f"test:{uuid.uuid4().hex[:12]}"
    return {"configurable": {"thread_id": tid}}


# ====================================================================
# Routing: duplicate event (priority 1)
# ====================================================================


@pytest.mark.asyncio
async def test_duplicate_event_does_not_advance(online_graph, config):
    """Same ``event_id`` on consecutive calls must return ``duplicate``."""
    event_id = str(uuid.uuid4())

    # Start a flow so we can verify it didn't advance
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id=str(uuid.uuid4())),
        config=config,
        context=_CONTEXT,
    )
    # Advance with a long message (>8 chars avoids SW clarification)
    first = await online_graph.ainvoke(
        _make_input("今天主动联系了一个一直没回复的客户", event_id=event_id),
        config=config,
        context=_CONTEXT,
    )
    # Same event_id again → should be duplicate
    duplicate = await online_graph.ainvoke(
        _make_input("今天主动联系了一个一直没回复的客户", event_id=event_id),
        config=config,
        context=_CONTEXT,
    )

    assert duplicate["flow_stage"] == first["flow_stage"]
    assert duplicate["response_kind"] == "duplicate"


@pytest.mark.asyncio
async def test_first_event_is_not_duplicate(online_graph, config):
    """A first-time ``event_id`` must not be flagged as duplicate."""
    result = await online_graph.ainvoke(
        _make_input("你好", event_id=str(uuid.uuid4())),
        config=config,
        context=_CONTEXT,
    )
    assert result.get("response_kind") != "duplicate"


# ====================================================================
# Routing: flow preemption (priority 2 beats 4)
# ====================================================================


@pytest.mark.asyncio
async def test_new_flow_preempts_existing_flow(online_graph, config):
    """A flow trigger while a different flow is active starts the new one."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    await online_graph.ainvoke(
        _make_input("一个真实进展", event_id="e2"),
        config=config,
        context=_CONTEXT,
    )
    switched = await online_graph.ainvoke(
        _make_input("访前准备", event_id="e3"),
        config=config,
        context=_CONTEXT,
    )

    assert switched["active_flow"] == "visit_preparation"
    assert switched["flow_stage"] == "customer"
    assert switched["flow_payload"] == {}


# ====================================================================
# Routing: cancel beats advance (priority 3 beats 4)
# ====================================================================


@pytest.mark.asyncio
async def test_cancel_clears_active_flow(online_graph, config):
    """A cancel command while a flow is active must end the flow."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    result = await online_graph.ainvoke(
        _make_input("退出", event_id="e2"),
        config=config,
        context=_CONTEXT,
    )

    assert result["active_flow"] is None
    assert result["flow_stage"] is None
    assert result["flow_payload"] is None
    assert result["response_kind"] == "flow_cancelled"


# ====================================================================
# Routing: guided flows disabled → always chat
# ====================================================================


@pytest.mark.asyncio
async def test_guided_disabled_routes_to_chat(online_graph, config):
    """When ``guided_flows_enabled=False``, even trigger phrases route to chat."""
    result = await online_graph.ainvoke(
        _make_input("小赢欣赏", guided_flows_enabled=False),
        config=config,
        context=_CONTEXT,
    )
    assert result["response_kind"] == "chat"


@pytest.mark.asyncio
async def test_guided_disabled_no_trigger_routes_to_chat(online_graph, config):
    """No trigger + guided disabled = chat."""
    result = await online_graph.ainvoke(
        _make_input("你好", guided_flows_enabled=False),
        config=config,
        context=_CONTEXT,
    )
    assert result["response_kind"] == "chat"


# ====================================================================
# Routing: no active flow / no trigger → chat (priority 5)
# ====================================================================


@pytest.mark.asyncio
async def test_no_flow_trigger_routes_to_chat(online_graph, config):
    """A non-trigger message with no active flow routes to chat."""
    result = await online_graph.ainvoke(
        _make_input("今天天气怎么样"),
        config=config,
        context=_CONTEXT,
    )
    assert result["response_kind"] == "chat"


# ====================================================================
# State persistence
# ====================================================================


@pytest.mark.asyncio
async def test_fresh_saver_has_no_active_flow(online_graph, config):
    """A brand-new thread must not carry any active flow."""
    result = await online_graph.ainvoke(
        _make_input("你好"),
        config=config,
        context=_CONTEXT,
    )
    assert result.get("active_flow") is None


@pytest.mark.asyncio
async def test_same_thread_carries_active_flow(online_graph, config):
    """State checkpointing must carry ``active_flow`` across turns."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    advanced = await online_graph.ainvoke(
        _make_input("今天主动联系了一个客户", event_id="e2"),
        config=config,
        context=_CONTEXT,
    )
    assert advanced["active_flow"] == "small_win_appreciation"
    assert advanced["flow_stage"] == "strength"


# ====================================================================
# last_event_id tracking
# ====================================================================


@pytest.mark.asyncio
async def test_last_event_id_updated_on_non_duplicate(online_graph, config):
    """Every non-duplicate turn must update ``last_event_id``."""
    await online_graph.ainvoke(
        _make_input("你好", event_id="ev-001"),
        config=config,
        context=_CONTEXT,
    )
    state = await online_graph.ainvoke(
        _make_input("你好", event_id="ev-002"),
        config=config,
        context=_CONTEXT,
    )
    assert state.get("last_event_id") == "ev-002"


@pytest.mark.asyncio
async def test_duplicate_does_not_update_last_event_id(online_graph, config):
    """A duplicate must NOT change ``last_event_id``."""
    event_id = "ev-dup"
    await online_graph.ainvoke(
        _make_input("你好", event_id=event_id),
        config=config,
        context=_CONTEXT,
    )
    dup = await online_graph.ainvoke(
        _make_input("你好", event_id=event_id),
        config=config,
        context=_CONTEXT,
    )
    assert dup["response_kind"] == "duplicate"
    # last_event_id should still be "ev-dup" from the first message
    assert dup.get("last_event_id") == event_id


# ====================================================================
# Thread ID format
# ====================================================================


def test_thread_id_format():
    """Thread ID must match the ``online:`` format with Shanghai date."""
    now = datetime(2026, 7, 6, 10, 30, 0)
    tid = build_online_thread_id(
        "t1", "a1", "dingtalk", "u1", now=now, timezone_name="Asia/Shanghai",
    )
    assert tid == "online:t1:a1:dingtalk:u1:2026-07-06"


def test_thread_id_changes_with_date():
    """Different dates produce different thread IDs."""
    day1 = datetime(2026, 7, 6, 10, 0, 0)
    day2 = datetime(2026, 7, 7, 10, 0, 0)
    tid1 = build_online_thread_id("t1", "a1", "dingtalk", "u1", now=day1)
    tid2 = build_online_thread_id("t1", "a1", "dingtalk", "u1", now=day2)
    assert tid1 != tid2


def test_thread_id_separates_users():
    """Different ``session_user_id`` values get different thread IDs."""
    now = datetime(2026, 7, 6, 10, 0, 0)
    tid1 = build_online_thread_id("t1", "a1", "dingtalk", "u1", now=now)
    tid2 = build_online_thread_id("t1", "a1", "dingtalk", "u2", now=now)
    assert tid1 != tid2


def test_thread_id_same_day_same_user_is_identical():
    """Same user on the same day gets the same thread ID (deterministic)."""
    now = datetime(2026, 7, 6, 10, 0, 0)
    tid1 = build_online_thread_id("t1", "a1", "dingtalk", "u1", now=now)
    tid2 = build_online_thread_id("t1", "a1", "dingtalk", "u1", now=now)
    assert tid1 == tid2
