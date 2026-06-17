"""Phase 3：积分 / 段位 / 等级 / 里程碑解锁。

在每日评估成功后调用 ``apply_progression``：
1. 计算当日积分（有效对话/话题/质量信号），按每日 50 分上限。
2. 累加到档案 total_points，按段位表更新 rank（**不降级**）。
3. 按 old/new 分数差解锁维度里程碑 + 全维度里程碑（每个仅一次）。
4. 按「已解锁里程碑数」更新 level。

积分来源（spec）：
- 有效销售对话 +10，有效销售话题 +5，质量行为信号 +1..+5。
- 每日积分上限 50。
- 里程碑解锁在首版**不**额外加分（避免双重激励）。

首版 rank 不降级；competency 分数可下降，但已解锁里程碑不撤销。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.constants import (
    DIMENSIONS,
    DAILY_POINTS_CAP,
    RANK_TABLE,
    level_for_milestones,
    rank_for_points,
)
from sales_agent.coach.milestones import (
    all_dimension_milestone_id,
    dimension_milestone_id,
)
from sales_agent.models.coach import (
    CoachMilestone,
    CoachUserMilestone,
    CoachUserProfile,
)

logger = logging.getLogger(__name__)


def compute_daily_points(payload: dict[str, Any], *, conversation_count: int) -> int:
    """根据评估 payload 与当日会话数计算当日积分（未封顶前的原始值）。"""
    points_block = payload.get("points") if isinstance(payload, dict) else None
    pts = 0
    if isinstance(points_block, dict):
        try:
            pts += max(0, int(points_block.get("conversation_points", 0) or 0))
            pts += max(0, int(points_block.get("topic_points", 0) or 0))
            pts += max(0, int(points_block.get("quality_signal_points", 0) or 0))
        except (TypeError, ValueError):
            pts = 0
    # 兜底：当 LLM 未给 points 时，按有效对话数估算
    if pts == 0 and conversation_count > 0:
        pts = min(DAILY_POINTS_CAP, conversation_count * 10)
    return min(pts, DAILY_POINTS_CAP)


class ProgressionService:
    """积分/段位/等级/里程碑解锁。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def apply_progression(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_id: str,
        evaluation_date: str,
        payload: dict[str, Any],
        score_moves: dict[str, tuple[int, int]],
        conversation_count: int,
        profile: CoachUserProfile,
    ) -> dict[str, Any]:
        """应用一次评估的成长进展，返回变更摘要。

        ``score_moves``: {dimension: (old_score, new_score)}。
        """
        daily_points = compute_daily_points(payload, conversation_count=conversation_count)

        # 1. 积分 + 段位（不降级）
        prev_points = int(profile.total_points or 0)
        new_total = prev_points + daily_points
        new_rank_key, _ = rank_for_points(new_total)
        prev_rank_idx = self._rank_index(profile.rank)
        new_rank_idx = self._rank_index(new_rank_key)
        # 不降级
        if new_rank_idx < prev_rank_idx:
            kept_rank = RANK_TABLE[prev_rank_idx][0]
            rank_changed = False
        else:
            kept_rank = new_rank_key
            rank_changed = new_rank_idx > prev_rank_idx

        profile.total_points = new_total
        profile.rank = kept_rank

        # 2. 里程碑解锁
        unlocked = await self._unlock_milestones(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            evaluation_id=evaluation_id, score_moves=score_moves,
        )

        # 3. 等级（按已解锁里程碑总数）
        total_unlocked = await self._count_unlocked(tenant_id, agent_id, user_id)
        profile.level = level_for_milestones(total_unlocked)

        await self.db.flush()

        return {
            "daily_points": daily_points,
            "total_points": new_total,
            "rank": kept_rank,
            "rank_changed": rank_changed,
            "milestones_unlocked": unlocked,
            "total_milestones": total_unlocked,
            "level": profile.level,
        }

    # ------------------------------------------------------------------

    async def _unlock_milestones(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_id: str,
        score_moves: dict[str, tuple[int, int]],
    ) -> list[dict[str, Any]]:
        """维度 old<threshold<=new + 全维度 all-six>=threshold。仅一次。"""
        unlocked: list[dict[str, Any]] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # 维度里程碑候选
        for dim, (old_s, new_s) in score_moves.items():
            if new_s <= old_s:
                continue  # 只有上升才可能解锁
            for threshold in self._thresholds_crossed(old_s, new_s):
                ms_id = dimension_milestone_id(dim, threshold)
                if await self._try_unlock(
                    tenant_id, agent_id, user_id, ms_id, evaluation_id, new_s, now_iso
                ):
                    unlocked.append({"milestone_id": ms_id, "dimension": dim, "threshold": threshold})

        # 全维度里程碑：六个当前分数都 >= threshold
        cur_scores = {dim: new for dim, (_, new) in score_moves.items()}
        for dim_key, _ in DIMENSIONS:
            cur_scores.setdefault(dim_key, 0)
        for threshold in (5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            if all(s >= threshold for s in cur_scores.values()):
                ms_id = all_dimension_milestone_id(threshold)
                if await self._try_unlock(
                    tenant_id, agent_id, user_id, ms_id, evaluation_id, 0, now_iso
                ):
                    unlocked.append({"milestone_id": ms_id, "dimension": None, "threshold": threshold})
        return unlocked

    async def _try_unlock(
        self, tenant_id: str, agent_id: str, user_id: str,
        milestone_id: str, evaluation_id: str, trigger_score: int, now_iso: str,
    ) -> bool:
        """解锁一个里程碑（唯一约束保证幂等）。返回是否本次新增。"""
        existing = (
            await self.db.execute(
                select(CoachUserMilestone).where(
                    CoachUserMilestone.tenant_id == tenant_id,
                    CoachUserMilestone.agent_id == agent_id,
                    CoachUserMilestone.user_id == user_id,
                    CoachUserMilestone.milestone_id == milestone_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False
        self.db.add(CoachUserMilestone(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            milestone_id=milestone_id, unlocked_at=now_iso,
            trigger_score=trigger_score, source_evaluation_id=evaluation_id,
        ))
        return True

    @staticmethod
    def _thresholds_crossed(old_s: int, new_s: int) -> list[int]:
        from sales_agent.coach.constants import MILESTONE_THRESHOLDS
        return [t for t in MILESTONE_THRESHOLDS if old_s < t <= new_s]

    async def _count_unlocked(self, tenant_id: str, agent_id: str, user_id: str) -> int:
        from sqlalchemy import func
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(CoachUserMilestone)
                .where(
                    CoachUserMilestone.tenant_id == tenant_id,
                    CoachUserMilestone.agent_id == agent_id,
                    CoachUserMilestone.user_id == user_id,
                )
            )
        ).scalar()
        return int(total or 0)

    @staticmethod
    def _rank_index(rank_key: str) -> int:
        for i, (k, _, _) in enumerate(RANK_TABLE):
            if k == rank_key:
                return i
        return 0
