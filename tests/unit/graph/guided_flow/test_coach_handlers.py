import pytest

from sales_agent.graph.guided_flow.handlers.coach_flows import (
    advance_sales_block,
    advance_small_win,
    start_sales_block,
    start_small_win,
)
from sales_agent.graph.guided_flow.types import FlowServices


SERVICES = FlowServices(db=None, chat_model=None, tenant_id="t1", agent_id="a1")


@pytest.mark.asyncio
async def test_small_win_advances_one_turn():
    started = start_small_win()
    result = await advance_small_win(
        started.stage,
        started.payload,
        "今天主动联系了一个一直没回复的客户",
        SERVICES,
    )
    assert result.stage == "strength"
    assert result.completed is False
    assert "做得不错" in result.reply


@pytest.mark.asyncio
async def test_sales_block_finishes_with_fallback_card():
    started = start_sales_block()
    first = await advance_sales_block(
        started.stage, started.payload, "客户嫌贵一直不回", SERVICES
    )
    second = await advance_sales_block(
        first.stage, first.payload, "事实是嫌贵；我判断没戏，担心被拒", SERVICES
    )
    final = await advance_sales_block(
        second.stage, second.payload, "可能怕买错，先问清风险", SERVICES
    )
    assert final.completed is True
    assert "最小行动" in final.reply
