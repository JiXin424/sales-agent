"""ChatPipeline 中的教练报告拦截集成测试。

验证：教练报告意图被提前拦截、直接渲染报告返回、不走正常任务路由；
正常销售消息不被"偷走"。
"""

from __future__ import annotations

import pytest
import sales_agent.core.tenant_runtime as tr_mod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import LatencyConfig, PathRouterConfig, Settings
from sales_agent.models.agent import Agent
from sales_agent.models.coach import (
    CoachCompetencyScore,
    CoachReportRequest,
    CoachUserProfile,
)
from sales_agent.models.conversation import Conversation
from sales_agent.services.chat_pipeline import ChatPipeline


@pytest.fixture
async def coach_pipeline_env(db_session: AsyncSession, sample_tenant: str):
    """配置 dedicated runtime + 默认 Agent + 用户教练数据，返回 (agent, settings)。"""
    # dedicated runtime，tenant 匹配 sample_tenant，无需真实 model（报告是纯读）。
    # 但 TenantResolver.get_model_provider 在 model_provider 为 None 时会尝试构造真实
    # provider（触发 OpenAI 凭证校验），因此提供一个 stub provider 让其短路。
    class _StubProvider:
        chat = None
        embedding = None

    rt = tr_mod.TenantRuntime(
        tenant_id=sample_tenant, tenant_name="t", deployment_mode="dedicated",
        model_provider=_StubProvider(),
    )
    saved = tr_mod._runtime
    tr_mod._runtime = rt
    try:
        agent = Agent(
            tenant_id=sample_tenant, name="Coach Agent", agent_type="sales_assistant",
            status="active", is_tenant_default=True,
        )
        db_session.add(agent)
        await db_session.flush()
        # 用户教练数据
        db_session.add(CoachUserProfile(
            tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
            total_points=100, rank="silver", level=1,
        ))
        for dim, score in {
            "customer_identification": 60, "needs_discovery": 50, "value_delivery": 70,
            "trust_building": 55, "deal_advancement": 45, "review_reflection": 50,
        }.items():
            db_session.add(CoachCompetencyScore(
                tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
                dimension=dim, score=score, last_delta=0,
            ))
        await db_session.flush()

        s = Settings()
        s.latency = LatencyConfig()
        s.path_router = PathRouterConfig()
        yield agent, s
    finally:
        tr_mod._runtime = saved


@pytest.mark.asyncio
async def test_scores_intent_intercepts_pipeline(
    db_session: AsyncSession, coach_pipeline_env
):
    agent, settings = coach_pipeline_env
    pipeline = ChatPipeline(db_session, settings)
    result = await pipeline.execute(
        tenant_id=agent.tenant_id, user_id="u1",
        message="我的评分", conversation_id="conv_coach_1",
        agent_id=agent.id,
    )
    # 返回的是报告，而非正常销售回答
    assert "客户识别" in result.answer_dict["summary"] or result.answer_dict["sections"]
    # route_result 为 None（未走正常路由）
    assert result.route_result is None

    # 写入了报告请求审计
    req = (await db_session.execute(
        select(CoachReportRequest).where(CoachReportRequest.agent_id == agent.id)
    )).scalars().all()
    assert len(req) == 1
    assert req[0].report_type == "scores"

    # 会话日志 task_type=coach_report
    conv = (await db_session.execute(
        select(Conversation).where(Conversation.id == "conv_coach_1")
    )).scalar_one()
    assert conv.task_type == "coach_report"


@pytest.mark.asyncio
async def test_iceberg_and_full_intents(db_session: AsyncSession, coach_pipeline_env):
    agent, settings = coach_pipeline_env
    pipeline = ChatPipeline(db_session, settings)
    # 冰山：无冰山数据时优雅返回
    r1 = await pipeline.execute(
        tenant_id=agent.tenant_id, user_id="u1",
        message="冰山", conversation_id="conv_ice", agent_id=agent.id,
    )
    assert r1.route_result is None
    # 教练报告（full）
    r2 = await pipeline.execute(
        tenant_id=agent.tenant_id, user_id="u1",
        message="教练报告", conversation_id="conv_full", agent_id=agent.id,
    )
    assert r2.route_result is None
    titles = [s["title"] for s in r2.answer_dict["sections"]]
    assert "六维能力" in titles


@pytest.mark.asyncio
async def test_normal_sales_message_not_intercepted(
    db_session: AsyncSession, coach_pipeline_env
):
    """正常销售消息不被教练分支拦截（即使后续因无 model 抛错）。"""
    agent, settings = coach_pipeline_env
    pipeline = ChatPipeline(db_session, settings)
    with pytest.raises(Exception):
        await pipeline.execute(
            tenant_id=agent.tenant_id, user_id="u1",
            message="客户说太贵了怎么回", conversation_id="conv_sales",
            agent_id=agent.id,
        )
    # 没有产生教练报告请求 → 证明分支未被进入
    req = (await db_session.execute(
        select(CoachReportRequest).where(CoachReportRequest.agent_id == agent.id)
    )).scalars().all()
    assert req == []


@pytest.mark.asyncio
async def test_conversation_scoring_not_stolen_by_coach(
    db_session: AsyncSession, coach_pipeline_env
):
    """"给我打分"应保留为 conversation_scoring，不被教练报告偷走。"""
    agent, settings = coach_pipeline_env
    pipeline = ChatPipeline(db_session, settings)
    with pytest.raises(Exception):
        await pipeline.execute(
            tenant_id=agent.tenant_id, user_id="u1",
            message="给我打分", conversation_id="conv_scoring",
            agent_id=agent.id,
        )
    req = (await db_session.execute(
        select(CoachReportRequest).where(CoachReportRequest.agent_id == agent.id)
    )).scalars().all()
    assert req == []
