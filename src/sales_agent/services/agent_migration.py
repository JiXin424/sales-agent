"""Agent 迁移与回填工具。

为每个 tenant 创建一个默认 Agent，并把既有运行/运维数据回填到该默认 Agent，
保证 tenant-only 调用在引入 Agent 后仍可用（向后兼容）。

幂等：可重复运行；已存在默认 Agent 的 tenant 不会被重复创建。
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent import Agent
from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope
from sales_agent.models.agent_prompt_set import AgentPromptSet
from sales_agent.models.tenant import Tenant

logger = logging.getLogger(__name__)

# 需要回填 agent_id 的运行/运维表及其 ORM 模型类。
# 这些表都有 tenant_id + 可空 agent_id 列。
_RUNTIME_TABLES = (
    "conversations",
    "retrieval_logs",
    "conversation_messages",
    "conversation_summaries",
    "agent_runs",
    "agent_run_steps",
    "feedbacks",
    "review_items",
    "knowledge_gaps",
    "model_call_logs",
    "eval_runs",
    "alerts",
    "alert_rules",
    "pilot_reports",
)


def _runtime_models() -> Iterable[type]:
    """返回需要回填 agent_id 的 ORM 模型类（惰性导入避免循环依赖）。"""
    from sales_agent.models.conversation import (
        Conversation,
        ConversationMessage,
        ConversationSummary,
        RetrievalLog,
    )
    from sales_agent.models.agent_run import AgentRun, AgentRunStep
    from sales_agent.models.feedback import Feedback
    from sales_agent.models.review_item import ReviewItem
    from sales_agent.models.knowledge_gap import KnowledgeGap
    from sales_agent.models.model_call_log import ModelCallLog
    from sales_agent.models.eval import EvalRun
    from sales_agent.models.alert import Alert, AlertRule
    from sales_agent.models.pilot_report import PilotReport

    return [
        Conversation, RetrievalLog, ConversationMessage, ConversationSummary,
        AgentRun, AgentRunStep, Feedback, ReviewItem, KnowledgeGap,
        ModelCallLog, EvalRun, Alert, AlertRule, PilotReport,
    ]


async def ensure_default_agent_for_tenant(db: AsyncSession, tenant_id: str, tenant_name: str) -> Agent:
    """为指定 tenant 确保存在一个默认 Agent；不存在则创建并回填数据。

    返回该 tenant 的默认 Agent。
    """
    # 1. 是否已有默认 Agent
    stmt = select(Agent).where(
        Agent.tenant_id == tenant_id,
        Agent.is_tenant_default == True,  # noqa: E712
    ).limit(1)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    # 2. 创建默认 Agent（active，使用 TenantRuntime 模型配置）
    default_name = f"{tenant_name} Sales Agent"
    agent = Agent(
        tenant_id=tenant_id,
        name=default_name,
        agent_type="sales_assistant",
        description="迁移自动创建的默认 Agent（兼容 tenant-only 调用）",
        status="active",
        model_config_ref="runtime",
        feature_flags_json="{}",
        is_tenant_default=True,
    )
    db.add(agent)
    await db.flush()  # 拿到 agent.id

    # 3. 默认 prompt set（空映射 → 运行时回退到 tenant/Python 默认）
    prompt_set = AgentPromptSet(
        agent_id=agent.id,
        tenant_id=tenant_id,
        name="default",
        task_prompt_versions_json="{}",
    )
    db.add(prompt_set)
    await db.flush()
    agent.prompt_set_id = prompt_set.id

    # 4. 默认知识作用域（tenant_all，兼容旧行为）
    scope = AgentKnowledgeScope(
        agent_id=agent.id,
        tenant_id=tenant_id,
        mode="tenant_all",
        document_ids_json="[]",
        source_file_ids_json="[]",
    )
    db.add(scope)
    await db.flush()
    agent.knowledge_scope_id = scope.id

    # 5. 回填该 tenant 的既有运行/运维数据到默认 Agent
    await _backfill_tenant_rows(db, tenant_id, agent.id)

    logger.info(
        "Created default Agent %s for tenant %s (%s), backfilled runtime rows.",
        agent.id, tenant_id, default_name,
    )
    return agent


async def _backfill_tenant_rows(db: AsyncSession, tenant_id: str, agent_id: str) -> int:
    """把该 tenant 下 agent_id IS NULL 的运行/运维行回填为 agent_id。返回回填总数。"""
    total = 0
    for model in _runtime_models():
        stmt = (
            update(model)
            .where(
                model.tenant_id == tenant_id,
                model.agent_id.is_(None),
            )
            .values(agent_id=agent_id)
        )
        # 注意：asyncpg 下 result.rowcount 会触发 MissingGreenlet，改用同步返回。
        await db.execute(stmt)
        total += 1
    return total


async def ensure_default_agents(db: AsyncSession) -> dict[str, Agent]:
    """遍历所有 tenant，为每个 tenant 确保默认 Agent 存在。

    用于 init_db() 启动钩子，保证向后兼容。
    返回 {tenant_id: default_agent}。
    """
    result = await db.execute(select(Tenant))
    tenants = list(result.scalars().all())

    mapping: dict[str, Agent] = {}
    for tenant in tenants:
        agent = await ensure_default_agent_for_tenant(db, tenant.id, tenant.name)
        mapping[tenant.id] = agent

    if tenants:
        logger.info("ensure_default_agents: %d tenant(s) processed", len(tenants))
    return mapping
