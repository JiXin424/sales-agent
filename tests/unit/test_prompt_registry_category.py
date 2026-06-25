"""验证 PromptRegistry 的通用 category 解析（system/router/risk）与双 schema 兼容（阶段2）。"""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_prompt_set import AgentPromptSet
from sales_agent.models.prompt import PromptVersion
from sales_agent.services.prompt_registry import PromptRegistry


async def _make_agent(db: AsyncSession, tenant_id: str, name: str = "A"):
    """建一个 Agent（不预绑 prompt_set，由测试自行设置，与 test_agent_runtime 一致）。"""
    from sales_agent.services.agent_service import AgentService

    svc = AgentService(db)
    d = await svc.create_agent({"tenant_id": tenant_id, "name": name})
    return await svc.get_agent(d["id"])


@pytest.mark.asyncio
async def test_resolve_system_builtin_default(db_session: AsyncSession, sample_tenant: str):
    """无 DB 版本时，system prompt 回退到内置默认。"""
    reg = PromptRegistry(db_session)
    tpl = await reg.resolve_prompt("system", "system_constraint", sample_tenant)
    assert "ToB 企业销售陪跑 Agent" in tpl


@pytest.mark.asyncio
async def test_resolve_system_tenant_active(db_session: AsyncSession, sample_tenant: str):
    """tenant active 系统型 prompt 覆盖内置默认。"""
    db_session.add(PromptVersion(
        tenant_id=sample_tenant, prompt_category="system", prompt_key="system_constraint",
        task_type=None, status="active", template_text="CUSTOM SYSTEM",
    ))
    await db_session.flush()
    reg = PromptRegistry(db_session)
    tpl = await reg.resolve_prompt("system", "system_constraint", sample_tenant)
    assert tpl == "CUSTOM SYSTEM"


@pytest.mark.asyncio
async def test_resolve_risk_builtin_default(db_session: AsyncSession, sample_tenant: str):
    reg = PromptRegistry(db_session)
    tpl = await reg.resolve_prompt("risk", "risk_check", sample_tenant)
    assert "合规检查员" in tpl


@pytest.mark.asyncio
async def test_resolve_router_builtin_default(db_session: AsyncSession, sample_tenant: str):
    reg = PromptRegistry(db_session)
    tpl = await reg.resolve_prompt("router", "task_router", sample_tenant)
    assert "任务分类器" in tpl


@pytest.mark.asyncio
async def test_create_non_task_version_no_message_required(db_session: AsyncSession, sample_tenant: str):
    """非 task 类 prompt 不强制 {message}。"""
    reg = PromptRegistry(db_session)
    pv = await reg.create_version(
        tenant_id=sample_tenant, task_type="",
        template_text="自定义系统约束（无需占位符）",
        prompt_category="system", prompt_key="system_constraint",
    )
    assert pv.prompt_category == "system"
    assert pv.prompt_key == "system_constraint"
    assert pv.task_type is None


@pytest.mark.asyncio
async def test_create_task_still_validates_message(db_session: AsyncSession, sample_tenant: str):
    """task 类仍要求 {message} 占位符。"""
    reg = PromptRegistry(db_session)
    with pytest.raises(ValueError):
        await reg.create_version(
            tenant_id=sample_tenant, task_type="knowledge_qa",
            template_text="no placeholder here",
        )


@pytest.mark.asyncio
async def test_agent_prompt_set_nested_schema_system(db_session: AsyncSession, sample_tenant: str):
    """AgentPromptSet 新嵌套 schema：system 级 Agent 绑定生效。"""
    agent = await _make_agent(db_session, sample_tenant, "nested")
    reg = PromptRegistry(db_session)
    pv = await reg.create_version(
        tenant_id=sample_tenant, task_type="",
        template_text="AGENT SYSTEM", prompt_category="system",
        prompt_key="system_constraint",
    )
    mapping = {"system": {"system_constraint": pv.id}}
    ps = AgentPromptSet(
        agent_id=agent.id, tenant_id=sample_tenant,
        task_prompt_versions_json=json.dumps(mapping),
    )
    db_session.add(ps)
    await db_session.flush()  # 先持久化 ps 拿稳定 id
    agent.prompt_set_id = ps.id
    await db_session.flush()

    tpl = await reg.resolve_prompt("system", "system_constraint", sample_tenant, agent.id)
    assert tpl == "AGENT SYSTEM"


@pytest.mark.asyncio
async def test_agent_prompt_set_legacy_flat_schema(db_session: AsyncSession, sample_tenant: str):
    """旧扁平 schema（task 类）仍能正确解析（向后兼容）。"""
    agent = await _make_agent(db_session, sample_tenant, "legacy")
    reg = PromptRegistry(db_session)
    pv = await reg.create_version(
        tenant_id=sample_tenant, task_type="knowledge_qa",
        template_text="LEGACY {message}",
    )
    mapping = {"knowledge_qa": pv.id}  # 旧扁平格式
    ps = AgentPromptSet(
        agent_id=agent.id, tenant_id=sample_tenant,
        task_prompt_versions_json=json.dumps(mapping),
    )
    db_session.add(ps)
    await db_session.flush()  # 先持久化 ps 拿稳定 id
    agent.prompt_set_id = ps.id
    await db_session.flush()

    tpl = await reg.resolve_prompt("task", "knowledge_qa", sample_tenant, agent.id)
    assert tpl == "LEGACY {message}"


@pytest.mark.asyncio
async def test_activate_archives_same_category_key(db_session: AsyncSession, sample_tenant: str):
    """激活 system 版本时，同 (category,key) 的旧 active 自动归档。"""
    reg = PromptRegistry(db_session)
    old = await reg.create_version(
        tenant_id=sample_tenant, task_type="",
        template_text="OLD", prompt_category="system", prompt_key="system_constraint",
    )
    await reg.activate_version(sample_tenant, old.id)
    new = await reg.create_version(
        tenant_id=sample_tenant, task_type="",
        template_text="NEW {message}".replace(" {message}", ""),  # system 无需占位
        prompt_category="system", prompt_key="system_constraint",
    )
    await reg.activate_version(sample_tenant, new.id)
    await db_session.flush()
    assert old.status == "archived"
    assert new.status == "active"
