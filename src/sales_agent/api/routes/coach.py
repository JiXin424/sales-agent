"""Coach Growth System API 路由。

全部以 Agent 为作用域：``/agents/{agent_id}/coach/...``。
每个路由通过 ``_load_agent``（复用自 agents 路由）做 dedicated 租户归属校验，
并强制 ``(tenant_id, agent_id)`` 过滤。

Phase 1+2：手动每日评估、用户报告、评估记录、设置、rerun(=dry_run)、
最小 dashboard。Milestones/Rewards 返回空（Phase 3 填充）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.routes.agents import _load_agent
from sales_agent.coach.constants import DIMENSIONS, INITIAL_SCORE, REPORT_TYPES, dimension_label
from sales_agent.coach.daily_evaluator import DailyEvaluationService
from sales_agent.coach.report_service import CoachReportService
from sales_agent.models.coach import (
    CoachCompetencyScore,
    CoachDailyEvaluation,
    CoachReward,
    CoachSettings,
    CoachUserProfile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/coach", tags=["coach"])


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class RunDailyRequest(BaseModel):
    user_id: str | None = None
    date: str | None = None
    dry_run: bool = False
    force_recompute: bool = False


class SettingsUpdate(BaseModel):
    realtime_enabled: bool | None = None
    daily_evaluation_enabled: bool | None = None
    daily_evaluation_time: str | None = None
    timezone: str | None = None
    minimum_user_messages: int | None = None
    daily_realtime_guidance_limit: int | None = None
    daily_reward_notification_limit: int | None = None
    initial_score: int | None = None
    allow_negative_delta: bool | None = None
    voice_rewards_enabled: bool | None = None
    red_packet_reminders_enabled: bool | None = None
    evidence_quote_max_chars: int | None = None


class RewardPatch(BaseModel):
    status: str | None = None
    admin_note: str | None = None


# ---------------------------------------------------------------------------
# 用户报告
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/report")
async def get_report(
    agent_id: str,
    user_id: str,
    db: DbSession,
    type: str = Query("full", description="scores|level|iceberg|milestones|rewards|full"),
    viewer_user_id: str | None = Query(None, description="销售用户本人 id（RBAC 未实现时由调用方传入）"),
):
    """渲染指定类型的教练报告（只读）。"""
    if type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"未知报告类型: {type}")
    agent = await _load_agent(db, agent_id)
    svc = CoachReportService(db)
    report = await svc.render_report(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        user_id=user_id,
        report_type=type,
        viewer_user_id=viewer_user_id,
        query_text="",
    )
    return report


@router.get("/users/{user_id}")
async def get_user_profile(agent_id: str, user_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    profile = await _load_profile(db, agent.tenant_id, agent.id, user_id)
    scores = await _load_scores(db, agent.tenant_id, agent.id, user_id)
    if profile is None and not scores:
        return {
            "user_id": user_id,
            "has_data": False,
            "message": "尚无教练数据",
        }
    return {
        "user_id": user_id,
        "has_data": True,
        "rank": profile.rank if profile else "bronze",
        "level": profile.level if profile else 0,
        "total_points": profile.total_points if profile else 0,
        "enabled": profile.enabled if profile else True,
        "last_evaluated_date": profile.last_evaluated_date if profile else None,
        "scores": [
            {"dimension": s.dimension, "dimension_label": dimension_label(s.dimension),
             "score": s.score, "last_delta": s.last_delta}
            for s in scores
        ],
    }


@router.get("/users/{user_id}/scores")
async def get_user_scores(agent_id: str, user_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    scores = await _load_scores(db, agent.tenant_id, agent.id, user_id)
    return {
        "user_id": user_id,
        "scores": [
            {"dimension": s.dimension, "dimension_label": dimension_label(s.dimension),
             "score": s.score, "last_delta": s.last_delta,
             "last_evaluated_at": s.last_evaluated_at}
            for s in scores
        ],
    }


@router.get("/users/{user_id}/iceberg")
async def get_user_iceberg(agent_id: str, user_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    report = await CoachReportService(db).render_report(
        tenant_id=agent.tenant_id, agent_id=agent.id,
        user_id=user_id, report_type="iceberg",
    )
    return {"user_id": user_id, "report": report}


@router.get("/users/{user_id}/observations")
async def get_user_observations(
    agent_id: str, user_id: str, db: DbSession,
    limit: int = Query(20, ge=1, le=200),
):
    agent = await _load_agent(db, agent_id)
    from sales_agent.models.coach import CoachCompetencyObservation
    rows = (
        await db.execute(
            select(CoachCompetencyObservation)
            .where(
                CoachCompetencyObservation.tenant_id == agent.tenant_id,
                CoachCompetencyObservation.agent_id == agent.id,
                CoachCompetencyObservation.user_id == user_id,
            )
            .order_by(CoachCompetencyObservation.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    import json
    return {
        "user_id": user_id,
        "observations": [
            {
                "evaluation_date": r.evaluation_date,
                "dimension": r.dimension,
                "dimension_label": dimension_label(r.dimension),
                "delta": r.delta, "old_score": r.old_score, "new_score": r.new_score,
                "reason": r.reason,
                "evidence_quotes": json.loads(r.evidence_quotes_json or "[]"),
                "confidence": r.confidence,
            }
            for r in rows
        ],
    }


@router.get("/users/{user_id}/milestones")
async def get_user_milestones(agent_id: str, user_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    from sales_agent.models.coach import CoachUserMilestone
    total = (
        await db.execute(
            select(func.count())
            .select_from(CoachUserMilestone)
            .where(
                CoachUserMilestone.tenant_id == agent.tenant_id,
                CoachUserMilestone.agent_id == agent.id,
                CoachUserMilestone.user_id == user_id,
            )
        )
    ).scalar() or 0
    return {"user_id": user_id, "unlocked_count": int(total), "items": []}


@router.get("/users/{user_id}/rewards")
async def get_user_rewards(
    agent_id: str, user_id: str, db: DbSession,
    limit: int = Query(20, ge=1, le=200),
):
    agent = await _load_agent(db, agent_id)
    rows = (
        await db.execute(
            select(CoachReward)
            .where(
                CoachReward.tenant_id == agent.tenant_id,
                CoachReward.agent_id == agent.id,
                CoachReward.user_id == user_id,
            )
            .order_by(CoachReward.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "user_id": user_id,
        "items": [
            {"id": r.id, "reward_type": r.reward_type, "status": r.status,
             "message": r.message, "delivered_at": r.delivered_at}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# 管理接口
# ---------------------------------------------------------------------------


@router.post("/admin/run_daily")
async def run_daily(agent_id: str, req: RunDailyRequest, db: DbSession):
    """手动触发每日评估（Phase 1 的核心入口）。"""
    agent = await _load_agent(db, agent_id)
    svc = DailyEvaluationService(db)
    result = await svc.run_for_agent(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        user_id=req.user_id,
        date=req.date,
        dry_run=req.dry_run,
        force_recompute=req.force_recompute,
    )
    return result


@router.get("/evaluations")
async def list_evaluations(
    agent_id: str, db: DbSession,
    user_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    conds = [
        CoachDailyEvaluation.tenant_id == agent.tenant_id,
        CoachDailyEvaluation.agent_id == agent.id,
    ]
    if user_id:
        conds.append(CoachDailyEvaluation.user_id == user_id)
    if status:
        conds.append(CoachDailyEvaluation.status == status)
    total = (
        await db.execute(
            select(func.count()).select_from(CoachDailyEvaluation).where(*conds)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(CoachDailyEvaluation)
            .where(*conds)
            .order_by(CoachDailyEvaluation.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": r.id, "user_id": r.user_id, "evaluation_date": r.evaluation_date,
                "status": r.status, "conversation_count": r.conversation_count,
                "user_message_count": r.user_message_count,
                "points_delta": r.points_delta, "latency_ms": r.latency_ms,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.post("/evaluations/{evaluation_id}/rerun")
async def rerun_evaluation(agent_id: str, evaluation_id: str, db: DbSession):
    """重跑某次评估。Phase 1：force_recompute 被禁，rerun = dry_run 预览。"""
    agent = await _load_agent(db, agent_id)
    row = (
        await db.execute(
            select(CoachDailyEvaluation).where(
                CoachDailyEvaluation.id == evaluation_id,
                CoachDailyEvaluation.tenant_id == agent.tenant_id,
                CoachDailyEvaluation.agent_id == agent.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="评估记录不存在")
    svc = DailyEvaluationService(db)
    result = await svc.run_for_agent(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        user_id=row.user_id,
        date=row.evaluation_date,
        dry_run=True,  # Phase 1: 只读预览
        force_recompute=False,
    )
    return result


@router.get("/users")
async def list_users(
    agent_id: str, db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出该 Agent 下有教练档案的用户。"""
    agent = await _load_agent(db, agent_id)
    conds = [CoachUserProfile.tenant_id == agent.tenant_id, CoachUserProfile.agent_id == agent.id]
    total = (
        await db.execute(
            select(func.count()).select_from(CoachUserProfile).where(*conds)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(CoachUserProfile).where(*conds)
            .order_by(CoachUserProfile.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "items": [
            {"user_id": r.user_id, "rank": r.rank, "level": r.level,
             "total_points": r.total_points, "last_evaluated_date": r.last_evaluated_date}
            for r in rows
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/rewards")
async def list_rewards(
    agent_id: str, db: DbSession,
    user_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    agent = await _load_agent(db, agent_id)
    conds = [CoachReward.tenant_id == agent.tenant_id, CoachReward.agent_id == agent.id]
    if user_id:
        conds.append(CoachReward.user_id == user_id)
    total = (
        await db.execute(
            select(func.count()).select_from(CoachReward).where(*conds)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(CoachReward).where(*conds)
            .order_by(CoachReward.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "items": [
            {"id": r.id, "user_id": r.user_id, "reward_type": r.reward_type,
             "status": r.status, "message": r.message,
             "delivered_at": r.delivered_at, "created_at": r.created_at}
            for r in rows
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.patch("/rewards/{reward_id}")
async def patch_reward(agent_id: str, reward_id: str, req: RewardPatch, db: DbSession):
    agent = await _load_agent(db, agent_id)
    row = (
        await db.execute(
            select(CoachReward).where(
                CoachReward.id == reward_id,
                CoachReward.tenant_id == agent.tenant_id,
                CoachReward.agent_id == agent.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="奖励记录不存在")
    if req.status is not None:
        row.status = req.status
    await db.flush()
    return {"id": row.id, "status": row.status}


@router.get("/settings")
async def get_settings(agent_id: str, db: DbSession):
    agent = await _load_agent(db, agent_id)
    row = await _load_settings(db, agent.tenant_id, agent.id)
    return _settings_to_dict(row)


@router.patch("/settings")
async def patch_settings(agent_id: str, req: SettingsUpdate, db: DbSession):
    agent = await _load_agent(db, agent_id)
    row = await _load_settings(db, agent.tenant_id, agent.id)
    if row is None:
        row = CoachSettings(
            tenant_id=agent.tenant_id, agent_id=agent.id,
        )
        db.add(row)
        await db.flush()
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            setattr(row, k, v)
    await db.flush()
    return _settings_to_dict(row)


@router.get("/dashboard")
async def get_dashboard(agent_id: str, db: DbSession):
    """最小 dashboard：今日评估数 + 六维均分（完整聚合留待 Phase 5）。"""
    agent = await _load_agent(db, agent_id)
    today = (datetime.now(timezone.utc).date()).isoformat()

    evaluated_today = (
        await db.execute(
            select(func.count())
            .select_from(CoachDailyEvaluation)
            .where(
                CoachDailyEvaluation.tenant_id == agent.tenant_id,
                CoachDailyEvaluation.agent_id == agent.id,
                CoachDailyEvaluation.status == "success",
                CoachDailyEvaluation.evaluation_date >= today,
            )
        )
    ).scalar() or 0

    skipped_failed = (
        await db.execute(
            select(func.count())
            .select_from(CoachDailyEvaluation)
            .where(
                CoachDailyEvaluation.tenant_id == agent.tenant_id,
                CoachDailyEvaluation.agent_id == agent.id,
                CoachDailyEvaluation.status.in_(("skipped", "failed")),
            )
        )
    ).scalar() or 0

    # 六维均分
    avg_by_dim: dict[str, float] = {}
    rows = (
        await db.execute(
            select(CoachCompetencyScore.dimension, func.avg(CoachCompetencyScore.score))
            .where(
                CoachCompetencyScore.tenant_id == agent.tenant_id,
                CoachCompetencyScore.agent_id == agent.id,
            )
            .group_by(CoachCompetencyScore.dimension)
        )
    ).all()
    for dim, avg in rows:
        avg_by_dim[dim] = round(float(avg), 1) if avg is not None else INITIAL_SCORE

    radar = [
        {"dimension": key, "dimension_label": label, "avg_score": avg_by_dim.get(key, INITIAL_SCORE)}
        for key, label in DIMENSIONS
    ]
    return {
        "evaluated_today": int(evaluated_today),
        "skipped_or_failed_total": int(skipped_failed),
        "avg_scores": radar,
        "note": "Phase 1+2 最小看板；完整趋势/榜单/卡点聚合在 Phase 5。",
    }


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _load_profile(
    db: AsyncSession, tenant_id: str, agent_id: str, user_id: str
) -> CoachUserProfile | None:
    return (
        await db.execute(
            select(CoachUserProfile).where(
                CoachUserProfile.tenant_id == tenant_id,
                CoachUserProfile.agent_id == agent_id,
                CoachUserProfile.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def _load_scores(
    db: AsyncSession, tenant_id: str, agent_id: str, user_id: str
) -> list[CoachCompetencyScore]:
    rows = (
        await db.execute(
            select(CoachCompetencyScore).where(
                CoachCompetencyScore.tenant_id == tenant_id,
                CoachCompetencyScore.agent_id == agent_id,
                CoachCompetencyScore.user_id == user_id,
            )
        )
    ).scalars().all()
    return list(rows)


async def _load_settings(
    db: AsyncSession, tenant_id: str, agent_id: str
) -> CoachSettings | None:
    return (
        await db.execute(
            select(CoachSettings).where(
                CoachSettings.tenant_id == tenant_id,
                CoachSettings.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()


def _settings_to_dict(row: CoachSettings | None) -> dict[str, Any]:
    if row is None:
        return {
            "configured": False,
            "realtime_enabled": False,
            "daily_evaluation_enabled": True,
            "daily_evaluation_time": "23:00",
            "timezone": "Asia/Shanghai",
            "minimum_user_messages": 3,
            "daily_realtime_guidance_limit": 3,
            "daily_reward_notification_limit": 3,
            "initial_score": INITIAL_SCORE,
            "allow_negative_delta": True,
            "voice_rewards_enabled": False,
            "red_packet_reminders_enabled": False,
            "evidence_quote_max_chars": 160,
        }
    return {
        "configured": True,
        "realtime_enabled": row.realtime_enabled,
        "daily_evaluation_enabled": row.daily_evaluation_enabled,
        "daily_evaluation_time": row.daily_evaluation_time,
        "timezone": row.timezone,
        "minimum_user_messages": row.minimum_user_messages,
        "daily_realtime_guidance_limit": row.daily_realtime_guidance_limit,
        "daily_reward_notification_limit": row.daily_reward_notification_limit,
        "initial_score": row.initial_score,
        "allow_negative_delta": row.allow_negative_delta,
        "voice_rewards_enabled": row.voice_rewards_enabled,
        "red_packet_reminders_enabled": row.red_packet_reminders_enabled,
        "evidence_quote_max_chars": row.evidence_quote_max_chars,
    }
