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
- Strict get_online_graph requires initialized runtime
- initialize_online_runtime compiles exactly once
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.graph.online.graph import build_online_graph
from sales_agent.graph.online.state import OnlineConversationState
from sales_agent.graph.checkpoint_runtime import CheckpointUnavailableError
from sales_agent.services.online_conversation import (
    build_online_thread_id,
    build_online_turn_input,
    get_online_graph,
    initialize_online_runtime,
    _online_graph,
)
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
    "topic_routing_enabled": True,
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
    assert result["flow_payload"] == {}
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
# Routing: topic routing disabled → direct chat (bypasses context)
# ====================================================================


@pytest.mark.asyncio
async def test_topic_routing_disabled_bypasses_context_resolution(online_graph, config):
    """When ``topic_routing_enabled=False``, messages bypass context resolution
    (no clarify/topic management) but still classify intent via
    direct_evidence_routing so knowledge questions can retrieve."""
    ctx = {
        "db": None,
        "chat_model": None,
        "chat_runner": StubChatRunner(),
        "context_resolver_override": AsyncMock(),
        "evidence_router_override": AsyncMock(
            return_value=_make_evidence_decision(
                intent="knowledge_qa", knowledge_policy="required",
                retrieval_query="客户访前准备",
            ),
        ),
    }
    result = await online_graph.ainvoke(
        _make_input("什么样的客户适合做访前准备", topic_routing_enabled=False),
        config=config,
        context=ctx,
    )
    assert result["response_kind"] == "chat"
    # context_status is not set when bypassing context resolution
    assert result.get("context_status") is None
    # context resolver is bypassed on the direct_chat path
    ctx["context_resolver_override"].assert_not_awaited()
    # but intent classification still runs so knowledge questions retrieve
    ctx["evidence_router_override"].assert_awaited_once()
    assert result.get("knowledge_policy") == "required"
    assert result.get("needs_retrieval") is True


@pytest.mark.asyncio
async def test_direct_chat_required_policy_sets_needs_retrieval(online_graph, config):
    """direct_chat path: a knowledge query (policy=required) sets
    needs_retrieval=True so the chat subgraph fans out retrieval."""
    result = await online_graph.ainvoke(
        _make_input("福多多标准卡包含什么权益", topic_routing_enabled=False),
        config=config,
        context={
            "db": None, "chat_model": None, "chat_runner": StubChatRunner(),
            "evidence_router_override": AsyncMock(
                return_value=_make_evidence_decision(
                    intent="knowledge_qa", knowledge_policy="required",
                    response_mode="retrieve", retrieval_query="福多多标准卡权益",
                ),
            ),
        },
    )
    assert result["response_kind"] == "chat"
    assert result["knowledge_policy"] == "required"
    assert result["needs_retrieval"] is True


@pytest.mark.asyncio
async def test_direct_chat_none_policy_skips_retrieval(online_graph, config):
    """direct_chat path: a chitchat query (policy=none) sets
    needs_retrieval=False so the chat subgraph skips retrieval."""
    result = await online_graph.ainvoke(
        _make_input("你好", topic_routing_enabled=False),
        config=config,
        context={
            "db": None, "chat_model": None, "chat_runner": StubChatRunner(),
            "evidence_router_override": AsyncMock(
                return_value=_make_evidence_decision(
                    intent="emotional_support", knowledge_policy="none",
                ),
            ),
        },
    )
    assert result["response_kind"] == "chat"
    assert result["knowledge_policy"] == "none"
    assert result["needs_retrieval"] is False


@pytest.mark.asyncio
async def test_topic_routing_disabled_no_context_status(online_graph, config):
    """When ``topic_routing_enabled=False``, no topic-related state is set."""
    result = await online_graph.ainvoke(
        _make_input("你好", topic_routing_enabled=False),
        config=config,
        context={
            "db": None, "chat_model": None, "chat_runner": StubChatRunner(),
        },
    )
    assert result["response_kind"] == "chat"
    assert result.get("context_status") is None
    assert result.get("topic_id") is None
    assert result.get("standalone_query") is None


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


def test_thread_id_is_stable_across_midnight():
    before = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    after = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    assert before == after == "online:t1:a1:dingtalk:u1"


def test_thread_id_scope_changes_for_each_identity_dimension():
    base = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    variants = {
        build_online_thread_id("t2", "a1", "dingtalk", "u1"),
        build_online_thread_id("t1", "a2", "dingtalk", "u1"),
        build_online_thread_id("t1", "a1", "web", "u1"),
        build_online_thread_id("t1", "a1", "dingtalk", "u2"),
    }
    assert base not in variants
    assert len(variants) == 4


def test_new_turn_input_clears_transient_fields_but_not_thread_state():
    turn = build_online_turn_input(
        tenant_id="t1", agent_id="a1", user_id="u1",
        session_user_id="du1", channel="dingtalk",
        conversation_id="c1", message="新消息", event_id="e2",
        entry_action=None, guided_flows_enabled=True,
        topic_routing_enabled=True,
    )
    assert turn["answer_dict"] == {}
    assert turn["response_kind"] == "pending"
    assert turn["completed_flow"] is None
    assert turn["turn_relation"] is None
    assert turn["retained_entities"] == []
    assert turn["knowledge_scope"] == []
    assert "active_flow" not in turn
    assert "flow_stage" not in turn
    assert "last_event_id" not in turn


# ====================================================================
# Strict get_online_graph and initialization
# ====================================================================


def test_default_online_graph_requires_initialized_runtime(monkeypatch):
    """Default get_online_graph() raises CheckpointUnavailableError if runtime not initialized."""
    # Clear any existing graph
    monkeypatch.setattr("sales_agent.services.online_conversation._online_graph", None)
    with pytest.raises(CheckpointUnavailableError):
        get_online_graph()


def test_get_online_graph_with_checkpointer_works_without_initialization():
    """get_online_graph(checkpointer=...) works even if runtime not initialized (for tests)."""
    checkpointer = InMemorySaver()
    graph = get_online_graph(checkpointer=checkpointer)
    assert graph is not None


@pytest.mark.asyncio
async def test_initialize_online_runtime_compiles_once(monkeypatch):
    """initialize_online_runtime() calls initialize_production_checkpointer once and _compile_online_graph once."""
    # Setup mocks
    mock_saver = object()
    mock_compiled = object()
    mock_init = AsyncMock(return_value=mock_saver)
    mock_compile = MagicMock(return_value=mock_compiled)

    # Patch the module-level variables and functions
    monkeypatch.setattr("sales_agent.services.online_conversation._online_graph", None)
    monkeypatch.setattr(
        "sales_agent.services.online_conversation.initialize_production_checkpointer",
        mock_init,
    )
    monkeypatch.setattr(
        "sales_agent.services.online_conversation._compile_online_graph",
        mock_compile,
    )

    # First call
    first = await initialize_online_runtime()
    # Second call (should return cached)
    second = await initialize_online_runtime()

    # Verify both calls return the same object
    assert first is second is mock_compiled
    # Verify init was called once
    mock_init.assert_awaited_once()
    # Verify compile was called once with the saver
    mock_compile.assert_called_once_with(mock_saver)


# ====================================================================
# Reset state-machine (Task 6)
# ====================================================================


def test_normalize_turn_reset_priority_after_duplicate():
    """reset_requested=True must NOT fire on a duplicate event — duplicate
    detection wins over reset so a redelivered reset cannot clear state twice."""
    from sales_agent.graph.online.nodes import normalize_turn_node

    state = {
        "guided_flows_enabled": True,
        "message": "重新开始",
        "event_id": "ev-1",
        "last_event_id": "ev-1",  # duplicate
        "reset_requested": True,
    }
    result = normalize_turn_node(state)
    assert result["flow_action"] == "duplicate"


def test_normalize_turn_reset_when_requested():
    """reset_requested=True (non-duplicate) sets flow_action='reset'."""
    from sales_agent.graph.online.nodes import normalize_turn_node

    state = {
        "guided_flows_enabled": True,
        "message": "重新开始",
        "event_id": "ev-1",
        "last_event_id": "ev-0",  # not duplicate
        "reset_requested": True,
    }
    result = normalize_turn_node(state)
    assert result["flow_action"] == "reset"


@pytest.mark.asyncio
async def test_reset_clears_active_flow(online_graph, config):
    """A reset_requested turn with an empty message clears active flow state
    and emits the reset confirmation, routing through log_control_response."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    result = await online_graph.ainvoke(
        _make_input("", event_id="e2", reset_requested=True),
        config=config,
        context=_CONTEXT,
    )
    assert result["active_flow"] is None
    assert result["flow_stage"] is None
    assert result["flow_payload"] == {}
    assert result.get("force_new_topic") is True
    assert result["turn_relation"] == "new"
    assert result["response_kind"] == "reset"
    assert "已开启新话题" in result["answer_dict"]["summary"]
    assert result["last_event_id"] == "e2"


@pytest.mark.asyncio
async def test_reset_with_remaining_message_reaches_chat(online_graph, config):
    """A reset with a remaining suffix clears flow state and routes the
    suffix through context_resolution -> evidence_routing -> chat in the
    SAME turn. force_new_topic makes context_resolution create a clean topic."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    result = await online_graph.ainvoke(
        _make_input("帮我写开场白", event_id="e2", reset_requested=True),
        config=config,
        context=_CONTEXT,
    )
    assert result["active_flow"] is None
    assert result["flow_stage"] is None
    assert result["response_kind"] == "chat"
    assert result.get("context_status") == "resolved"
    assert result.get("force_new_topic") is not True
    assert result["last_event_id"] == "e2"


@pytest.mark.asyncio
async def test_thread_id_unchanged_across_reset(online_graph):
    """The thread ID is stable across a reset — reset is an explicit state
    transition, not a new random conversation ID."""
    tid = "test:stable-reset-thread"
    cfg = {"configurable": {"thread_id": tid}}

    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=cfg,
        context=_CONTEXT,
    )
    await online_graph.ainvoke(
        _make_input("", event_id="e2", reset_requested=True),
        config=cfg,
        context=_CONTEXT,
    )
    state = await online_graph.aget_state(cfg)
    assert state.config["configurable"]["thread_id"] == tid
    assert state.values.get("active_flow") is None


@pytest.mark.asyncio
async def test_reset_does_not_advance_old_flow_on_next_message(online_graph, config):
    """After a reset, the next ordinary message does NOT advance the old
    guided flow — it routes to context_resolution / chat as a new turn."""
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=_CONTEXT,
    )
    await online_graph.ainvoke(
        _make_input("", event_id="e2", reset_requested=True),
        config=config,
        context=_CONTEXT,
    )
    result = await online_graph.ainvoke(
        _make_input("今天见了一个客户", event_id="e3"),
        config=config,
        context=_CONTEXT,
    )
    assert result["response_kind"] == "chat"
    assert result.get("active_flow") is None
    assert result.get("flow_stage") is None


# ====================================================================
# Persistence failure re-raises (Task 6 Step 6)
# ====================================================================


@pytest.mark.asyncio
async def test_log_control_response_node_raises_on_log_failure():
    """log_control_response_node must re-raise when log_conversation fails."""
    from sales_agent.graph.online.nodes import log_control_response_node
    from types import SimpleNamespace

    cfg = {
        "configurable": {
            "__pregel_runtime": SimpleNamespace(context={"db": AsyncMock()}),
        }
    }
    with patch(
        "sales_agent.graph.online.nodes.conversation_logger.log_conversation",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError, match="db down"):
            await log_control_response_node(
                {
                    "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
                    "channel": "dingtalk", "conversation_id": "c1",
                    "message": "reset", "event_id": "ev-1",
                    "answer_dict": {"summary": "已开启新话题"},
                },
                cfg,
            )


@pytest.mark.asyncio
async def test_log_flow_output_node_raises_on_log_failure():
    """log_flow_output_node must re-raise when log_conversation fails."""
    from sales_agent.graph.online.nodes import log_flow_output_node
    from types import SimpleNamespace

    cfg = {
        "configurable": {
            "__pregel_runtime": SimpleNamespace(context={"db": AsyncMock()}),
        }
    }
    with patch(
        "sales_agent.graph.online.nodes.conversation_logger.log_conversation",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError, match="db down"):
            await log_flow_output_node(
                {
                    "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
                    "channel": "dingtalk", "conversation_id": "c1",
                    "message": "advance", "event_id": "ev-1",
                    "active_flow": "small_win_appreciation",
                    "answer_dict": {"summary": "ok"},
                },
                cfg,
            )


@pytest.mark.asyncio
async def test_graph_guided_flow_log_failure_reraises(online_graph, config):
    """When log_flow_output_node's log_conversation raises, the Graph
    invocation re-raises and last_event_id is NOT advanced."""
    # Use a context that includes a truthy db so log_flow_output_node
    # actually calls log_conversation (it skips when db is None).
    live_ctx = {**{k: v for k, v in _CONTEXT.items()}, "db": MagicMock()}

    # Start a guided flow so active_flow is set.
    await online_graph.ainvoke(
        _make_input("小赢欣赏", event_id="e1"),
        config=config,
        context=live_ctx,
    )
    state_before = await online_graph.aget_state(config)
    prior_last_event_id = state_before.values.get("last_event_id")

    # Patch at the source module so ALL importers see the side-effect.
    with patch(
        "sales_agent.services.conversation_logger.log_conversation",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError, match="db down"):
            await online_graph.ainvoke(
                _make_input("今天主动联系了一个一直没回复的客户", event_id="e2"),
                config=config,
                context=live_ctx,
            )

    state_after = await online_graph.aget_state(config)
    assert state_after.values.get("last_event_id") == prior_last_event_id
