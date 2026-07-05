"""Tests for guided visit-flow handlers (访前准备 / 访后复盘).

Tests cover the deterministic fallback path (no LLM) and LLM-failure recovery.
"""

import pytest

from sales_agent.graph.guided_flow.handlers.visit_flows import (
    advance_post_visit,
    advance_visit_preparation,
    start_post_visit,
    start_visit_preparation,
)
from sales_agent.graph.guided_flow.types import FlowServices


SERVICES = FlowServices(db=None, chat_model=None, tenant_id="t1", agent_id="a1")


class _FailingModel:
    """Duck-typed mock whose ``generate`` always raises RuntimeError."""

    async def generate(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("model down")


_FAILING_SERVICES = FlowServices(
    db=None, chat_model=_FailingModel(), tenant_id="t1", agent_id="a1"
)


@pytest.mark.asyncio
async def test_visit_preparation_is_three_answers_then_card():
    state = start_visit_preparation()
    assert state.stage == "customer"
    first = await advance_visit_preparation(
        state.stage, state.payload, "上海某工会负责人", SERVICES
    )
    assert first.stage == "situation" and first.completed is False
    second = await advance_visit_preparation(
        first.stage, first.payload, "正在比较两家福利平台", SERVICES
    )
    assert second.stage == "goal" and second.completed is False
    final = await advance_visit_preparation(
        second.stage, second.payload, "确认试点范围和时间", SERVICES
    )
    assert final.completed is True
    assert "访前作战卡" in final.reply


@pytest.mark.asyncio
async def test_post_visit_is_three_answers_then_card():
    state = start_post_visit()
    first = await advance_post_visit(
        state.stage, state.payload, "客户认可方案但担心预算", SERVICES
    )
    second = await advance_post_visit(
        first.stage, first.payload, "有兴趣，仍在观望", SERVICES
    )
    final = await advance_post_visit(
        second.stage, second.payload, "周五前补预算测算", SERVICES
    )
    assert final.completed is True
    assert "机会推进卡" in final.reply


@pytest.mark.asyncio
async def test_visit_preparation_llm_failure_falls_back():
    """When chat_model.generate raises, terminal advance returns fallback card."""
    state = start_visit_preparation()
    first = await advance_visit_preparation(
        state.stage, state.payload, "上海某工会负责人", _FAILING_SERVICES
    )
    second = await advance_visit_preparation(
        first.stage, first.payload, "正在比较两家福利平台", _FAILING_SERVICES
    )
    final = await advance_visit_preparation(
        second.stage, second.payload, "确认试点范围和时间", _FAILING_SERVICES
    )
    assert final.completed is True
    assert "访前作战卡" in final.reply


@pytest.mark.asyncio
async def test_post_visit_llm_failure_falls_back():
    """When chat_model.generate raises, terminal advance returns fallback card."""
    state = start_post_visit()
    first = await advance_post_visit(
        state.stage, state.payload, "客户认可方案但担心预算", _FAILING_SERVICES
    )
    second = await advance_post_visit(
        first.stage, first.payload, "有兴趣，仍在观望", _FAILING_SERVICES
    )
    final = await advance_post_visit(
        second.stage, second.payload, "周五前补预算测算", _FAILING_SERVICES
    )
    assert final.completed is True
    assert "机会推进卡" in final.reply
