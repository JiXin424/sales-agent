"""Phase 3 集成测试：里程碑解锁/单次、全维度门槛、每日积分上限、段位不降级、奖励上限与合并。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.daily_evaluator import DailyEvaluationService
from sales_agent.coach.progression import ProgressionService, compute_daily_points
from sales_agent.coach.reward_service import (
    MAX_NOTIFICATIONS_PER_DAY,
    MAX_RED_PACKET_PER_DAY,
    RewardService,
)
from sales_agent.models.agent import Agent
from sales_agent.models.coach import (
    CoachReward,
    CoachUserMilestone,
    CoachUserProfile,
)
from sales_agent.models.conversation import Conversation, ConversationMessage

DATE = "2026-06-14"


class FakeChat:
    def __init__(self, payload: dict):
        self._payload = payload

    async def generate(self, *, messages, temperature=None, max_tokens=None, response_format=None):
        return json.dumps(self._payload, ensure_ascii=False)


async def _seed_messages(db, tenant_id, agent_id, user_id, *, date, user_count=3, conv_id="c1"):
    db.add(Conversation(
        id=conv_id, tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
        channel="local", message="seed", task_type="knowledge_qa", status="completed",
    ))
    await db.flush()
    for i in range(user_count):
        db.add(ConversationMessage(
            conversation_id=conv_id, tenant_id=tenant_id, agent_id=agent_id,
            user_id=user_id, role="user", content=f"msg{i}",
            created_at=f"{date}T10:{i:02d}:00+00:00",
        ))
    db.add(ConversationMessage(
        conversation_id=conv_id, tenant_id=tenant_id, agent_id=agent_id,
        user_id=user_id, role="assistant", content="reply",
        created_at=f"{date}T11:00:00+00:00",
    ))
    await db.flush()


async def _make_agent(db, tenant_id):
    a = Agent(tenant_id=tenant_id, name="A", status="active", is_tenant_default=True)
    db.add(a)
    await db.flush()
    return a


def _delta_payload(deltas: dict[str, int]) -> dict:
    """构造一个六维齐全的合法 payload，指定维度的 delta。"""
    base = {
        "data_sufficiency": "sufficient",
        "summary": "ok",
        "dimensions": {},
        "iceberg": {"surface_blocks": [], "deep_blocks": []},
        "next_growth_suggestion": "继续。",
        "points": {"conversation_points": 0, "topic_points": 0, "quality_signal_points": 0, "reason": ""},
    }
    quotes = ["证据原文"]
    for dim, d in deltas.items():
        if d == 0:
            base["dimensions"][dim] = {"delta": 0, "reason": "", "evidence_quotes": [],
                                        "source_conversation_ids": [], "confidence": 0.0}
        else:
            base["dimensions"][dim] = {"delta": d, "reason": "r", "evidence_quotes": quotes,
                                        "source_conversation_ids": ["c1"], "confidence": 0.8}
    # 补齐六维
    all_dims = ["customer_identification", "needs_discovery", "value_delivery",
                "trust_building", "deal_advancement", "review_reflection"]
    for dim in all_dims:
        base["dimensions"].setdefault(dim, {"delta": 0, "reason": "", "evidence_quotes": [],
                                            "source_conversation_ids": [], "confidence": 0.0})
    return base


# ---------------------------------------------------------------------------
# 里程碑解锁：维度 / 单次
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dimension_milestone_unlocks_when_crossing_threshold(
    db_session: AsyncSession, sample_tenant: str
):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE)
    # 预置起始分 48（低于 50 阈值），+3 → 51 跨过 50
    from sales_agent.models.coach import CoachCompetencyScore
    for dim in ("customer_identification", "needs_discovery"):
        db_session.add(CoachCompetencyScore(
            tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
            dimension=dim, score=48, last_delta=0,
        ))
    await db_session.flush()

    payload = _delta_payload({"customer_identification": 3, "needs_discovery": 3})
    svc = DailyEvaluationService(
        db_session, chat_model=FakeChat(payload),
        reward_sender=None, reward_rng=lambda: 1.0,  # 不触发随机奖励（1.0 不 < p）
    )
    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)

    unlocked = result["results"][0].get("progression", {}).get("milestones_unlocked", [])
    ids = [u["milestone_id"] for u in unlocked]
    assert "coach_ms__customer_identification__50" in ids
    assert "coach_ms__needs_discovery__50" in ids

    rows = (await db_session.execute(
        select(CoachUserMilestone).where(
            CoachUserMilestone.agent_id == agent.id, CoachUserMilestone.user_id == "u1"
        )
    )).scalars().all()
    unlocked_ids = {r.milestone_id for r in rows}
    assert "coach_ms__customer_identification__50" in unlocked_ids


@pytest.mark.asyncio
async def test_milestone_unlocks_only_once(
    db_session: AsyncSession, sample_tenant: str
):
    """已解锁的里程碑不会被重复解锁（幂等守卫）。"""
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE)
    from sales_agent.models.coach import CoachCompetencyScore
    db_session.add(CoachCompetencyScore(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        dimension="customer_identification", score=48, last_delta=0,
    ))
    # 预先插入已解锁记录，模拟"曾经解锁过"
    db_session.add(CoachUserMilestone(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        milestone_id="coach_ms__customer_identification__50",
        unlocked_at="2026-06-10T00:00:00+00:00", trigger_score=50,
    ))
    await db_session.flush()

    payload = _delta_payload({"customer_identification": 3})  # 48 → 51 跨 50
    svc = DailyEvaluationService(
        db_session, chat_model=FakeChat(payload),
        reward_sender=None, reward_rng=lambda: 1.0,
    )
    await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)

    rows = (await db_session.execute(
        select(CoachUserMilestone).where(
            CoachUserMilestone.user_id == "u1",
            CoachUserMilestone.milestone_id == "coach_ms__customer_identification__50",
        )
    )).scalars().all()
    assert len(rows) == 1  # 不重复解锁


# ---------------------------------------------------------------------------
# 全维度里程碑门槛
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_dimension_milestone_unlocks_when_all_six_meet_threshold(
    db_session: AsyncSession, sample_tenant: str
):
    agent = await _make_agent(db_session, sample_tenant)
    await _seed_messages(db_session, sample_tenant, agent.id, "u1", date=DATE)
    # 六维都 +1 → 50→51，全部 >=50，解锁 all@50
    payload = _delta_payload({d: 1 for d in [
        "customer_identification", "needs_discovery", "value_delivery",
        "trust_building", "deal_advancement", "review_reflection"]})

    svc = DailyEvaluationService(db_session, chat_model=FakeChat(payload),
                                  reward_sender=None, reward_rng=lambda: 1.0)
    result = await svc.run_for_agent(agent.id, tenant_id=sample_tenant, user_id="u1", date=DATE)
    unlocked = result["results"][0]["progression"]["milestones_unlocked"]
    ids = {u["milestone_id"] for u in unlocked}
    assert "coach_ms__all__50" in ids


# ---------------------------------------------------------------------------
# 每日积分上限 50
# ---------------------------------------------------------------------------


def test_daily_points_capped_at_50():
    payload = {"points": {"conversation_points": 100, "topic_points": 100, "quality_signal_points": 100}}
    assert compute_daily_points(payload, conversation_count=5) == 50


def test_daily_points_fallback_by_conversations():
    payload = {}  # LLM 未给 points
    # 2 个对话 * 10 = 20，未超上限
    assert compute_daily_points(payload, conversation_count=2) == 20
    # 10 个对话 * 10 = 100 → 封顶 50
    assert compute_daily_points(payload, conversation_count=10) == 50


# ---------------------------------------------------------------------------
# 段位不降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rank_does_not_downgrade(db_session: AsyncSession, sample_tenant: str):
    agent = await _make_agent(db_session, sample_tenant)
    # 预置一个白银段位（>=100 分）
    db_session.add(CoachUserProfile(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        enabled=True, total_points=150, rank="silver", level=1,
    ))
    await db_session.flush()
    # 直接测 progression：积分增加但目标段位低于当前 → 保持
    from sales_agent.coach.progression import ProgressionService
    svc = ProgressionService(db_session)
    prof = (await db_session.execute(
        select(CoachUserProfile).where(CoachUserProfile.user_id == "u1")
    )).scalar_one()
    # 这次 +0 积分（payload 无 points，conversation 0）
    res = await svc.apply_progression(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        evaluation_id="e1", evaluation_date=DATE,
        payload={}, score_moves={}, conversation_count=0, profile=prof,
    )
    assert res["rank"] == "silver"  # 未降级


# ---------------------------------------------------------------------------
# 奖励上限与合并
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_milestone_reward_merged_and_delivered(
    db_session: AsyncSession, sample_tenant: str
):
    agent = await _make_agent(db_session, sample_tenant)
    sent: list[str] = []

    async def fake_sender(user_id, text):
        sent.append(text)

    svc = RewardService(db_session, sender=fake_sender, rng=lambda: 0.0)
    unlocked = [
        {"milestone_id": "coach_ms__needs_discovery__50", "dimension": "needs_discovery", "threshold": 50},
        {"milestone_id": "coach_ms__value_delivery__50", "dimension": "value_delivery", "threshold": 50},
    ]
    created = await svc.grant_for_milestones(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        evaluation_id="e1", evaluation_date=DATE, unlocked=unlocked,
    )
    text_rewards = [c for c in created if c["reward_type"] == "text_milestone"]
    assert len(text_rewards) == 1  # 合并为一条
    assert text_rewards[0]["status"] == "delivered"
    assert len(sent) == 1
    assert "needs_discovery@50" in sent[0] and "value_delivery@50" in sent[0]


@pytest.mark.asyncio
async def test_reward_daily_notification_limit(
    db_session: AsyncSession, sample_tenant: str
):
    agent = await _make_agent(db_session, sample_tenant)
    unlocked = [{"milestone_id": f"m{i}", "dimension": "d", "threshold": 50} for i in range(10)]
    svc = RewardService(db_session, sender=None, rng=lambda: 0.0)
    created = await svc.grant_for_milestones(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        evaluation_id="e1", evaluation_date=DATE, unlocked=unlocked,
    )
    # 只有 1 条合并 text（badge/red_packet 因 rng=0 不触发）
    text_rewards = [c for c in created if c["reward_type"] == "text_milestone"]
    assert len(text_rewards) == 1


@pytest.mark.asyncio
async def test_red_packet_at_most_one_per_day(
    db_session: AsyncSession, sample_tenant: str
):
    agent = await _make_agent(db_session, sample_tenant)
    # 第一次：rng 恒触发 red packet
    svc = RewardService(db_session, sender=None, rng=lambda: 0.0)  # 0.0 < p 触发所有
    unlocked = [{"milestone_id": "m1", "dimension": "d", "threshold": 50}]
    await svc.grant_for_milestones(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        evaluation_id="e1", evaluation_date=DATE, unlocked=unlocked,
    )
    # 第二次同日：red packet 应被上限拦截
    await svc.grant_for_milestones(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1",
        evaluation_id="e2", evaluation_date=DATE, unlocked=unlocked,
    )
    rp = (await db_session.execute(
        select(CoachReward).where(
            CoachReward.agent_id == agent.id, CoachReward.user_id == "u1",
            CoachReward.reward_type == "red_packet_reminder",
        )
    )).scalars().all()
    assert len(rp) == 1
