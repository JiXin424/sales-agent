"""验证 prompt_resolver_helper（阶段2 公共解析入口）。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.services.prompt_resolver_helper import (
    resolve_execution_prompts,
    resolve_risk_prompt,
)


@pytest.mark.asyncio
async def test_resolve_execution_prompts_returns_task_and_system(
    db_session: AsyncSession, sample_tenant: str
):
    task_tpl, sys_tpl = await resolve_execution_prompts(
        db_session, None, sample_tenant, "knowledge_qa"
    )
    assert "知识库" in task_tpl
    assert "ToB 企业销售陪跑" in sys_tpl


@pytest.mark.asyncio
async def test_resolve_risk_prompt_default(db_session: AsyncSession, sample_tenant: str):
    tpl = await resolve_risk_prompt(db_session, sample_tenant)
    assert "合规检查员" in tpl


@pytest.mark.asyncio
async def test_resolve_execution_prompts_system_override(
    db_session: AsyncSession, sample_tenant: str
):
    """tenant active 的 system prompt 覆盖默认。"""
    from sales_agent.models.prompt import PromptVersion

    db_session.add(PromptVersion(
        tenant_id=sample_tenant, prompt_category="system", prompt_key="system_constraint",
        task_type=None, status="active", template_text="OVERRIDE SYSTEM",
    ))
    await db_session.flush()
    _, sys_tpl = await resolve_execution_prompts(
        db_session, None, sample_tenant, "knowledge_qa"
    )
    assert sys_tpl == "OVERRIDE SYSTEM"
