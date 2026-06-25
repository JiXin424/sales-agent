"""验证 Coach prompt 解耦（阶段3）：模板常量、defaults 注册、registry 解析。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.prompts.coach_quick import (
    SB_CARD_TEMPLATE,
    SB_SPLIT_TEMPLATE,
    SW_CARD_TEMPLATE,
)
from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS


def test_sw_card_template_formats():
    rendered = SW_CARD_TEMPLATE.format(
        small_win="X1", strength="X2", gratitude="X3", energy_sentence="X4",
    )
    for val in ("X1", "X2", "X3", "X4"):
        assert val in rendered


def test_sb_split_template_formats():
    rendered = SB_SPLIT_TEMPLATE.format(sales_input="S", user_split="U")
    assert "S" in rendered
    assert "U" in rendered


def test_sb_card_template_formats():
    rendered = SB_CARD_TEMPLATE.format(
        sales_input="S", split_text="SP", possibilities_attempt="P",
    )
    for val in ("S", "SP", "P"):
        assert val in rendered


def test_builtin_includes_coach_seven():
    """BUILTIN_PROMPTS 应含 coach 7 项。"""
    coach_keys = {b.key for b in BUILTIN_PROMPTS if b.category == "coach"}
    assert {
        "coach_daily_eval", "coach_daily_eval_system",
        "coach_sw_system", "coach_sb_system",
        "coach_sw_card", "coach_sb_split", "coach_sb_card",
    } <= coach_keys


@pytest.mark.asyncio
async def test_resolve_coach_prompts_builtin(db_session: AsyncSession, sample_tenant: str):
    """无 DB 版本时，coach 各 key 回退内置默认。"""
    from sales_agent.services.prompt_registry import PromptRegistry

    reg = PromptRegistry(db_session)
    sw_card = await reg.resolve_prompt("coach", "coach_sw_card", sample_tenant)
    assert "小赢卡" in sw_card
    sb_card = await reg.resolve_prompt("coach", "coach_sb_card", sample_tenant)
    assert "事实是什么" in sb_card
    daily = await reg.resolve_prompt("coach", "coach_daily_eval", sample_tenant)
    assert "{conversation_block}" in daily  # 模板含占位符


@pytest.mark.asyncio
async def test_resolve_coach_tenant_override(db_session: AsyncSession, sample_tenant: str):
    """tenant active 的 coach prompt 覆盖默认。"""
    from sales_agent.models.prompt import PromptVersion
    from sales_agent.services.prompt_registry import PromptRegistry

    db_session.add(PromptVersion(
        tenant_id=sample_tenant, prompt_category="coach", prompt_key="coach_sw_system",
        task_type=None, status="active", template_text="CUSTOM COACH SYSTEM",
    ))
    await db_session.flush()
    reg = PromptRegistry(db_session)
    tpl = await reg.resolve_prompt("coach", "coach_sw_system", sample_tenant)
    assert tpl == "CUSTOM COACH SYSTEM"
