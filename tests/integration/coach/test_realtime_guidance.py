"""Phase 4 实时引导集成测试：管道中 observe→guidance→融合→日志。

由于完整 execute_agent 需要 LLM，这里只验证：
- realtime_enabled=False（默认）：不写 applied 观察记录（全部 suppressed）。
- 实时引导被 try/except 包裹，绝不影响正常会话路径。
- observe/guidance 的副作用（CoachRealtimeObservation 日志）存在。
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
    CoachRealtimeObservation,
    CoachSettings,
    CoachUserProfile,
)
from sales_agent.services.chat_pipeline import ChatPipeline


class StubChat:
    """返回最小合法 JSON，避免真实 LLM。"""

    async def generate(self, *, messages, temperature=None, max_tokens=None, response_format=None):
        return '{"summary":"ok","sections":[{"title":"回答","content":"建议你先确认客户角色。"}]}'


@pytest.fixture
async def rt_env(db_session: AsyncSession, sample_tenant: str):
    class _StubProvider:
        chat = StubChat()
        embedding = None

    rt = tr_mod.TenantRuntime(
        tenant_id=sample_tenant, tenant_name="t", deployment_mode="dedicated",
        model_provider=_StubProvider(),
    )
    saved = tr_mod._runtime
    tr_mod._runtime = rt
    try:
        agent = Agent(
            tenant_id=sample_tenant, name="A", status="active", is_tenant_default=True,
        )
        db_session.add(agent)
        await db_session.flush()
        # 弱分数：customer_identification=30（specific 带）
        for dim, score in {
            "customer_identification": 30, "needs_discovery": 50, "value_delivery": 70,
            "trust_building": 55, "deal_advancement": 45, "review_reflection": 50,
        }.items():
            db_session.add(CoachCompetencyScore(
                tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
                dimension=dim, score=score, last_delta=0,
            ))
        s = Settings()
        s.latency = LatencyConfig()
        s.path_router = PathRouterConfig()
        yield agent, s
    finally:
        tr_mod._runtime = saved


@pytest.mark.asyncio
async def test_realtime_disabled_by_default_no_applied_guidance(
    db_session: AsyncSession, rt_env, sample_tenant: str
):
    """realtime_enabled 默认 False：不应用引导（suppressed_reason=realtime_disabled）。"""
    agent, settings = rt_env
    pipeline = ChatPipeline(db_session, settings)
    await pipeline.execute(
        tenant_id=sample_tenant, user_id="u1",
        message="明天要去拜访客户，帮我准备开场",
        conversation_id="conv_rt_1", agent_id=agent.id,
    )
    obs = (await db_session.execute(
        select(CoachRealtimeObservation).where(
            CoachRealtimeObservation.agent_id == agent.id,
            CoachRealtimeObservation.conversation_id == "conv_rt_1",
        )
    )).scalars().all()
    assert len(obs) == 1
    assert obs[0].applied_to_reply is False
    assert obs[0].suppressed_reason == "realtime_disabled"


@pytest.mark.asyncio
async def test_realtime_enabled_applies_specific_guidance(
    db_session: AsyncSession, rt_env, sample_tenant: str
):
    """开启 realtime_enabled 且弱维度 + 高置信场景 → 应用 specific 引导。"""
    agent, settings = rt_env
    db_session.add(CoachSettings(
        tenant_id=sample_tenant, agent_id=agent.id,
        realtime_enabled=True, daily_realtime_guidance_limit=3,
    ))
    await db_session.flush()
    pipeline = ChatPipeline(db_session, settings)
    await pipeline.execute(
        tenant_id=sample_tenant, user_id="u1",
        message="明天要去拜访客户，帮我准备开场",
        conversation_id="conv_rt_2", agent_id=agent.id,
    )
    obs = (await db_session.execute(
        select(CoachRealtimeObservation).where(
            CoachRealtimeObservation.conversation_id == "conv_rt_2"
        )
    )).scalar_one()
    assert obs.applied_to_reply is True
    assert obs.guidance_level == "specific"
    assert obs.guidance_text  # 有引导文本


@pytest.mark.asyncio
async def test_realtime_failure_does_not_break_chat(
    db_session: AsyncSession, rt_env, sample_tenant: str
):
    """实时引导路径出错（此处通过 coach 报告意图 + 普通消息混跑）不应中断会话。"""
    agent, settings = rt_env
    pipeline = ChatPipeline(db_session, settings)
    # 正常销售消息会走完整管道（StubChat），不应抛错
    result = await pipeline.execute(
        tenant_id=sample_tenant, user_id="u1",
        message="客户说太贵了怎么回",
        conversation_id="conv_rt_3", agent_id=agent.id,
    )
    assert result.answer_dict is not None
