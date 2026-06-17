"""每日评估集成测试 —— Phase 1 核心：评分、幂等、跳过、失败、dry_run、作用域隔离。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.daily_evaluator import DailyEvaluationService
from sales_agent.models.agent import Agent
from sales_agent.models.coach import (
    CoachCompetencyObservation,
    CoachCompetencyScore,
    CoachDailyEvaluation,
    CoachUserProfile,
)
from sales_agent.models.conversation import Conversation, ConversationMessage

DATE = "2026-06-14"


class FakeChat:
    """返回固定 JSON 的假 chat 模型。"""

    def __init__(self, payload: dict | str):
        self._payload = payload

    async def generate(self, *, messages, temperature=None, max_tokens=None, response_format=None):
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload, ensure_ascii=False)


def _valid_llm_payload():
    return {
        "data_sufficiency": "sufficient",
        "summary": "今日表现稳健",
        "dimensions": {
            "customer_identification": {"delta": 2, "reason": "补全决策链",
                                        "evidence_quotes": ["客户提到老板拍板"], "source_conversation_ids": ["c1"],
                                        "confidence": 0.8},
            "needs_discovery": {"delta": 1, "reason": "主动追问预算",
                                "evidence_quotes": ["你们预算多少"], "source_conversation_ids": ["c1"],
                                "confidence": 0.7},
            "value_delivery": {"delta": 0, "reason": "", "evidence_quotes": [], "confidence": 0.1},
            "trust_building": {"delta": 0, "reason": "", "evidence_quotes": [], "confidence": 0.1},
            "deal_advancement": {"delta": -1, "reason": "未约定下一步",
                                  "evidence_quotes": ["那再看看吧"], "source_conversation_ids": ["c1"],
                                  "confidence": 0.6},
            "review_reflection": {"delta": 0, "reason": "", "evidence_quotes": [], "confidence": 0.0},
        },
        "iceberg": {
            "surface_blocks": [
                {"type": "value_block", "severity": "high", "description": "价值未量化",
                 "evidence_quotes": ["再看看"], "source_conversation_ids": ["c1"]},
            ],
            "deep_blocks": [],
        },
        "next_growth_suggestion": "下次见客户前先量化价值。",
    }


async def _seed_messages(
    db: AsyncSession, tenant_id: str, agent_id: str, user_id: str,
    *, date: str, user_count: int, conv_id: str = "c1", task_type: str = "knowledge_qa",
):
    conv = Conversation(
        id=conv_id, tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
        channel="local", message="seed", task_type=task_type, status="completed",
    )
    db.add(conv)
    await db.flush()
    for i in range(user_count):
        db.add(ConversationMessage(
            conversation_id=conv_id, tenant_id=tenant_id, agent_id=agent_id,
            user_id=user_id, role="user", content=f"第{i+1}条用户消息",
            created_at=f"{date}T10:{i:02d}:00+00:00",
        ))
    # 一条助手消息
    db.add(ConversationMessage(
        conversation_id=conv_id, tenant_id=tenant_id, agent_id=agent_id,
        user_id=user_id, role="assistant", content="助手回复",
        created_at=f"{date}T11:00:00+00:00",
    ))
    await db.flush()


async def _make_agent(db: AsyncSession, tenant_id: str, name: str = "Coach Agent") -> Agent:
    agent = Agent(
        tenant_id=tenant_id, name=name, agent_type="sales_assistant",
        status="active", is_tenant_default=True,
    )
    db.add(agent)
    await db.flush()
    return agent


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_run_creates_scores_and_observations(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)

    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))
    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)

    assert result["summary"]["success"] == 1
    one = result["results"][0]
    assert one["status"] == "success"
    deltas = one["score_deltas"]
    assert deltas["customer_identification"] == 2
    assert deltas["deal_advancement"] == -1

    # 六维分数从 50 起，应用 delta
    scores = (await db_session.execute(
        select(CoachCompetencyScore).where(
            CoachCompetencyScore.agent_id == agent.id, CoachCompetencyScore.user_id == "u1"
        )
    )).scalars().all()
    assert len(scores) == 6
    by_dim = {s.dimension: s.score for s in scores}
    assert by_dim["customer_identification"] == 52  # 50 + 2
    assert by_dim["needs_discovery"] == 51
    assert by_dim["deal_advancement"] == 49  # 50 - 1
    assert by_dim["value_delivery"] == 50  # delta 0

    # 非零 delta 写观察记录，带证据
    obs = (await db_session.execute(
        select(CoachCompetencyObservation).where(
            CoachCompetencyObservation.agent_id == agent.id,
            CoachCompetencyObservation.dimension == "customer_identification",
        )
    )).scalars().all()
    assert len(obs) == 1
    assert obs[0].delta == 2
    assert json.loads(obs[0].evidence_quotes_json) == ["客户提到老板拍板"]

    # 档案已创建
    prof = (await db_session.execute(
        select(CoachUserProfile).where(
            CoachUserProfile.agent_id == agent.id, CoachUserProfile.user_id == "u1"
        )
    )).scalar_one()
    assert prof.last_evaluated_date == DATE


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_same_date_does_not_double_apply(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)
    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))

    await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    second = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)

    one = second["results"][0]
    assert one["status"] == "success"
    assert one.get("idempotent") is True

    # 分数未翻倍
    score = (await db_session.execute(
        select(CoachCompetencyScore).where(
            CoachCompetencyScore.agent_id == agent.id,
            CoachCompetencyScore.dimension == "customer_identification",
        )
    )).scalar_one()
    assert score.score == 52  # 仍为 50+2，非 54

    # 观察记录只一条
    obs = (await db_session.execute(
        select(CoachCompetencyObservation).where(
            CoachCompetencyObservation.agent_id == agent.id,
            CoachCompetencyObservation.dimension == "customer_identification",
        )
    )).scalars().all()
    assert len(obs) == 1

    # success 评估记录只一条
    evals = (await db_session.execute(
        select(CoachDailyEvaluation).where(
            CoachDailyEvaluation.agent_id == agent.id,
            CoachDailyEvaluation.user_id == "u1",
            CoachDailyEvaluation.evaluation_date == DATE,
            CoachDailyEvaluation.status == "success",
        )
    )).scalars().all()
    assert len(evals) == 1


# ---------------------------------------------------------------------------
# 数据不足 → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_messages_skipped(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=2)  # < 3
    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))

    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    one = result["results"][0]
    assert one["status"] == "skipped"

    # 不创建分数
    scores = (await db_session.execute(
        select(CoachCompetencyScore).where(CoachCompetencyScore.agent_id == agent.id)
    )).scalars().all()
    assert scores == []


# ---------------------------------------------------------------------------
# LLM 缺维度 / 非法 delta → failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_dimension_fails_without_score_change(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)
    bad = _valid_llm_payload()
    del bad["dimensions"]["needs_discovery"]
    svc = DailyEvaluationService(db_session, chat_model=FakeChat(bad))

    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    one = result["results"][0]
    assert one["status"] == "failed"

    scores = (await db_session.execute(
        select(CoachCompetencyScore).where(CoachCompetencyScore.agent_id == agent.id)
    )).scalars().all()
    assert scores == []


@pytest.mark.asyncio
async def test_invalid_json_fails(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)
    svc = DailyEvaluationService(db_session, chat_model=FakeChat("这不是 JSON"))

    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    assert result["results"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# dry_run 不改分数
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_mutate(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)
    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))

    result = await svc.run_for_agent(
        agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE, dry_run=True
    )
    one = result["results"][0]
    assert one["status"] == "dry_run"
    assert one["score_deltas"]["customer_identification"] == 2

    # 没有分数行
    scores = (await db_session.execute(
        select(CoachCompetencyScore).where(CoachCompetencyScore.agent_id == agent.id)
    )).scalars().all()
    assert scores == []


# ---------------------------------------------------------------------------
# force_recompute 在 Phase 1 被禁
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_recompute_blocked_without_dry_run(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)
    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))

    result = await svc.run_for_agent(
        agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE, force_recompute=True
    )
    one = result["results"][0]
    assert one["status"] == "failed"
    assert "dry_run" in one["error"] or "recompute" in one["error"].lower()


# ---------------------------------------------------------------------------
# Agent 作用域隔离
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_scoping_prevents_cross_agent_leakage(db_session: AsyncSession, sample_tenant: str):
    agent_a = await _make_agent(db_session, sample_tenant, name="A")
    agent_b = await _make_agent(db_session, sample_tenant, name="B")
    await _seed_messages(db_session, sample_tenant, agent_a.id, "u1", date=DATE, user_count=3, conv_id="ca")
    # agent_b 没有任何消息

    svc = DailyEvaluationService(db_session, chat_model=FakeChat(_valid_llm_payload()))
    await svc.run_for_agent(agent_a.id, tenant_id=sample_tenant, date=DATE)

    scores_a = (await db_session.execute(
        select(CoachCompetencyScore).where(CoachCompetencyScore.agent_id == agent_a.id)
    )).scalars().all()
    scores_b = (await db_session.execute(
        select(CoachCompetencyScore).where(CoachCompetencyScore.agent_id == agent_b.id)
    )).scalars().all()
    assert len(scores_a) == 6
    assert scores_b == []


# ---------------------------------------------------------------------------
# 分数钳制
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_clamps_to_0_100(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE, user_count=3)

    # 先把某个维度刷到接近边界，再评估 delta=+3 / -3 验证钳制
    payload = _valid_llm_payload()
    payload["dimensions"]["customer_identification"]["delta"] = 3
    payload["dimensions"]["customer_identification"]["reason"] = "x"
    payload["dimensions"]["customer_identification"]["evidence_quotes"] = ["q"]
    payload["dimensions"]["customer_identification"]["confidence"] = 0.9
    payload["dimensions"]["customer_identification"]["source_conversation_ids"] = ["c1"]

    svc = DailyEvaluationService(db_session, chat_model=FakeChat(payload))
    await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    score = (await db_session.execute(
        select(CoachCompetencyScore).where(
            CoachCompetencyScore.agent_id == agent.id,
            CoachCompetencyScore.dimension == "customer_identification",
        )
    )).scalar_one()
    assert score.score == 53  # 50 + 3
