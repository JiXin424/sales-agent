"""Agent 数据模型与迁移测试。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from sales_agent.models.agent import Agent
from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope
from sales_agent.models.agent_prompt_set import AgentPromptSet
from sales_agent.models.conversation import Conversation
from sales_agent.models.feedback import Feedback
from sales_agent.services.agent_migration import (
    ensure_default_agent_for_tenant,
    ensure_default_agents,
)


@pytest.mark.asyncio
async def test_new_agent_tables_created(db_session):
    """6 张 Agent 新表通过 create_all 创建并可写入。"""
    agent = Agent(
        tenant_id="t_new", name="新 Agent", status="draft", agent_type="sales_assistant"
    )
    db_session.add(agent)
    await db_session.flush()
    assert agent.id and len(agent.id) == 16
    assert agent.status == "draft"

    ps = AgentPromptSet(agent_id=agent.id, tenant_id="t_new")
    db_session.add(ps)
    scope = AgentKnowledgeScope(agent_id=agent.id, tenant_id="t_new", mode="document_subset",
                                document_ids_json=json.dumps(["d1"]))
    db_session.add(scope)
    await db_session.flush()
    assert ps.id and scope.id


@pytest.mark.asyncio
async def test_runtime_tables_have_nullable_agent_id(db_session, sample_tenant):
    """既有运行/运维表允许 agent_id 为空（向后兼容）。"""
    conv = Conversation(
        tenant_id=sample_tenant, user_id="u1", channel="local",
        message="hi", status="completed",
    )
    db_session.add(conv)
    await db_session.flush()
    assert conv.agent_id is None


@pytest.mark.asyncio
async def test_runtime_tables_accept_agent_id(db_session, sample_tenant):
    """运行表可写入 agent_id（Agent 作用域）。"""
    agent = Agent(tenant_id=sample_tenant, name="A1", status="active")
    db_session.add(agent)
    await db_session.flush()
    conv = Conversation(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        channel="local", message="hi", status="completed",
    )
    db_session.add(conv)
    await db_session.flush()
    assert conv.agent_id == agent.id


@pytest.mark.asyncio
async def test_ensure_default_agent_creates_one_per_tenant(db_session, sample_tenant):
    """每个 tenant 创建恰好一个默认 Agent。"""
    a1 = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    assert a1.is_tenant_default is True
    assert a1.status == "active"
    assert a1.model_config_ref == "runtime"
    assert a1.prompt_set_id is not None
    assert a1.knowledge_scope_id is not None

    # 幂等：再次调用返回同一个
    a2 = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    assert a2.id == a1.id

    # 只有一个默认 Agent
    stmt = select(Agent).where(
        Agent.tenant_id == sample_tenant, Agent.is_tenant_default == True  # noqa: E712
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ensure_default_agent_backfills_existing_rows(db_session, sample_tenant):
    """回填：既有 tenant 数据被关联到默认 Agent。"""
    from sqlalchemy import func

    # 先造既有数据（无 agent_id）
    conv = Conversation(
        tenant_id=sample_tenant, user_id="u1", channel="local",
        message="old", status="completed",
    )
    fb = Feedback(
        tenant_id=sample_tenant, conversation_id="c_x", user_id="u1",
        rating="down", review_status="open",
    )
    db_session.add_all([conv, fb])
    await db_session.flush()
    assert conv.agent_id is None

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    agent_id = agent.id

    # 回填后：该 tenant 下 agent_id 为默认 Agent 的行数应 >=2（conv + fb），
    # 且 agent_id 为 NULL 的行应为 0。
    null_conv = (await db_session.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.tenant_id == sample_tenant,
            Conversation.agent_id.is_(None),
        )
    )).scalar() or 0
    null_fb = (await db_session.execute(
        select(func.count()).select_from(Feedback).where(
            Feedback.tenant_id == sample_tenant,
            Feedback.agent_id.is_(None),
        )
    )).scalar() or 0
    linked_conv = (await db_session.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.tenant_id == sample_tenant,
            Conversation.agent_id == agent_id,
        )
    )).scalar() or 0
    assert null_conv == 0
    assert null_fb == 0
    assert linked_conv >= 1


@pytest.mark.asyncio
async def test_ensure_default_agent_no_cross_tenant_leak(db_session, sample_tenant):
    """回填不跨 tenant：另一个 tenant 的数据不被关联。"""
    from sqlalchemy import func

    # 另一个 tenant 的会话
    other_tenant = "other_tenant_xyz"
    other_conv = Conversation(
        tenant_id=other_tenant, user_id="u1", channel="local",
        message="other", status="completed",
    )
    db_session.add(other_conv)
    await db_session.flush()

    # 只为 sample_tenant 创建默认 Agent
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")

    # 另一个 tenant 的会话 agent_id 仍为 NULL
    null_other = (await db_session.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.tenant_id == other_tenant,
            Conversation.agent_id.is_(None),
        )
    )).scalar() or 0
    linked_other = (await db_session.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.tenant_id == other_tenant,
            Conversation.agent_id == agent.id,
        )
    )).scalar() or 0
    assert null_other == 1
    assert linked_other == 0


@pytest.mark.asyncio
async def test_ensure_default_agents_processes_all_tenants(db_session, sample_tenant):
    """ensure_default_agents 遍历所有 tenant。"""
    # sample_tenant 已存在（来自 fixture）
    mapping = await ensure_default_agents(db_session)
    assert sample_tenant in mapping
    assert mapping[sample_tenant].is_tenant_default is True
