"""Tests for the guided-flow registry, graph routing, and lifecycle.

Covers:

- Registry has exactly the four product flows.
- ``start`` → ``advance`` sequence across two separate ``ainvoke`` calls
  (the one-turn pattern).
- Same-flow restart resets ``flow_payload``.
- ``cancel`` clears all flow tracking fields.
- Full small-win flow reaches ``completed`` after four advances.
- Start node raises ``ValueError`` for unknown flow IDs.
- Cancel returns the specified summary text.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.graph.guided_flow.definitions import FLOW_DEFINITIONS, get_flow_definition
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph


# ====================================================================
# Registry
# ====================================================================


def test_registry_has_exactly_four_product_flows():
    assert set(FLOW_DEFINITIONS) == {
        "visit_preparation",
        "post_visit_review",
        "small_win_appreciation",
        "sales_block_breakthrough",
    }


def test_get_flow_definition_returns_known():
    for flow_id in FLOW_DEFINITIONS:
        definition = get_flow_definition(flow_id)
        assert definition.id == flow_id
        assert isinstance(definition.trigger_phrases, frozenset)
        assert len(definition.trigger_phrases) == 1


def test_get_flow_definition_raises_for_unknown():
    with pytest.raises(ValueError, match="unknown guided flow: nonexistent"):
        get_flow_definition("nonexistent")


# ====================================================================
# Graph routing — start, advance (one-turn), cancel, restart
# ====================================================================

THREAD = "flow:t1:a1:dingtalk:u1:2026-07-06"
BASE_INPUT = {
    "tenant_id": "t1",
    "agent_id": "a1",
    "user_id": "u1",
    "channel": "dingtalk",
    "conversation_id": "c1",
}
_CONTEXT = {"db": None, "chat_model": None}


@pytest.mark.asyncio
async def test_start_and_advance_are_separate_invocations():
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD}}

    started = await graph.ainvoke(
        {
            **BASE_INPUT,
            "message": "小赢欣赏",
            "flow_action": "start",
            "requested_flow": "small_win_appreciation",
        },
        config=config,
        context=_CONTEXT,
    )
    assert started["active_flow"] == "small_win_appreciation"
    assert started["flow_stage"] == "small_win"
    assert started["flow_payload"] == {}
    assert "小赢" in started["answer_dict"]["summary"]

    advanced = await graph.ainvoke(
        {"message": "今天主动联系了一个一直没回复的客户", "flow_action": "advance"},
        config=config,
        context=_CONTEXT,
    )
    assert advanced["flow_stage"] == "strength"
    assert advanced["active_flow"] == "small_win_appreciation"


@pytest.mark.asyncio
async def test_same_flow_start_resets_payload():
    """Starting the same flow a second time must reset the payload."""
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD + "_reset"}}

    await graph.ainvoke(
        {
            **BASE_INPUT,
            "message": "小赢欣赏",
            "flow_action": "start",
            "requested_flow": "small_win_appreciation",
        },
        config=config,
        context=_CONTEXT,
    )
    advanced = await graph.ainvoke(
        {"message": "今天主动联系了一个一直没回复的客户", "flow_action": "advance"},
        config=config,
        context=_CONTEXT,
    )
    assert advanced["flow_payload"].get("small_win")  # payload has data

    # Restart
    restarted = await graph.ainvoke(
        {"message": "小赢欣赏", "flow_action": "start", "requested_flow": "small_win_appreciation"},
        config=config,
        context=_CONTEXT,
    )
    assert restarted["flow_payload"] == {}


@pytest.mark.asyncio
async def test_cancel_clears_flow_state():
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD + "_cancel"}}

    await graph.ainvoke(
        {
            **BASE_INPUT,
            "message": "小赢欣赏",
            "flow_action": "start",
            "requested_flow": "small_win_appreciation",
        },
        config=config,
        context=_CONTEXT,
    )
    cancelled = await graph.ainvoke(
        {"message": "退出", "flow_action": "cancel"},
        config=config,
        context=_CONTEXT,
    )
    assert cancelled["active_flow"] is None
    assert cancelled["flow_stage"] is None
    assert cancelled["flow_payload"] == {}
    assert cancelled["completed_flow"] is None
    assert "已退出" in cancelled["answer_dict"]["summary"]


@pytest.mark.asyncio
async def test_full_small_win_flow_reaches_completed():
    """Four advances from ``small_win`` → ``strength`` → ``gratitude``
    → ``energy`` → ``completed``."""
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD + "_full"}}

    await graph.ainvoke(
        {
            **BASE_INPUT,
            "message": "小赢欣赏",
            "flow_action": "start",
            "requested_flow": "small_win_appreciation",
        },
        config=config,
        context=_CONTEXT,
    )

    replies = [
        "今天主动联系了一个一直没回复的客户",
        "坚持跟进不放弃",
        "感谢自己的耐心",
        "AI代写",
    ]
    for i, reply in enumerate(replies):
        result = await graph.ainvoke(
            {"message": reply, "flow_action": "advance"},
            config=config,
            context=_CONTEXT,
        )
        if i < len(replies) - 1:
            assert result["flow_stage"] != "completed"
            assert result["active_flow"] == "small_win_appreciation"
        else:
            assert result["completed_flow"] == "small_win_appreciation"
            assert result["active_flow"] is None

    # Verify final state
    assert result is not None
    assert result["completed_flow"] == "small_win_appreciation"
    assert result["active_flow"] is None


@pytest.mark.asyncio
async def test_completed_clears_flow_state():
    """After a flow completes, the flow-stage fields are None."""
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD + "_clear"}}

    await graph.ainvoke(
        {
            **BASE_INPUT,
            "message": "访前准备",
            "flow_action": "start",
            "requested_flow": "visit_preparation",
        },
        config=config,
        context=_CONTEXT,
    )
    await graph.ainvoke(
        {"message": "上海某工会负责人", "flow_action": "advance"},
        config=config,
        context=_CONTEXT,
    )
    await graph.ainvoke(
        {"message": "正在比较两家福利平台", "flow_action": "advance"},
        config=config,
        context=_CONTEXT,
    )
    final = await graph.ainvoke(
        {"message": "确认试点范围和时间", "flow_action": "advance"},
        config=config,
        context=_CONTEXT,
    )
    assert final["completed_flow"] == "visit_preparation"
    assert final["active_flow"] is None
    assert final["flow_stage"] is None
    assert final["flow_payload"] == {}
