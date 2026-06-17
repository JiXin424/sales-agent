"""Phase 3：奖励创建与派发。

规则（spec）：
- text_milestone 100%：创建记录 + 尝试发送钉钉文本。
- voice_encouragement 30%：仅创建 suggested 记录。
- badge 20%：创建 badge 奖励记录。
- red_packet_reminder 10%：仅创建管理员提醒记录。
- 每用户每天最多 3 条奖励通知。
- 同一天的多个里程碑文本消息合并。
- 每用户每天最多 1 条红包提醒。
- 数据不足（skipped）不发奖励。
- 负 delta 不撤销已解锁里程碑。
- 派发失败仅记 failed，不抛错（不影响会话）。

随机性由调用方注入（``rng``），便于测试。生产用 random.random。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.coach import CoachReward

logger = logging.getLogger(__name__)

REWARD_TEXT_MILESTONE = "text_milestone"
REWARD_VOICE = "voice_encouragement"
REWARD_BADGE = "badge"
REWARD_RED_PACKET = "red_packet_reminder"

REWARD_PROBABILITIES: dict[str, float] = {
    REWARD_TEXT_MILESTONE: 1.0,
    REWARD_VOICE: 0.3,
    REWARD_BADGE: 0.2,
    REWARD_RED_PACKET: 0.1,
}

MAX_NOTIFICATIONS_PER_DAY = 3
MAX_RED_PACKET_PER_DAY = 1


class RewardService:
    """奖励创建与派发。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        sender: Callable[[str, str], Any] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self._sender = sender  # async 或 sync：send_text(user_id, text)
        self._rng = rng or _default_rng

    async def grant_for_milestones(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_id: str,
        evaluation_date: str,
        unlocked: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """根据本次解锁的里程碑发放奖励（text 合并、随机其它类型）。

        返回本次创建的奖励记录摘要列表。
        """
        if not unlocked:
            return []

        # 预算检查：当日通知数已达上限则不再发
        notified_today = await self._count_today(
            tenant_id, agent_id, user_id, evaluation_date,
            exclude_types=(REWARD_VOICE,),  # suggested voice 不占通知预算
        )
        remaining_notify = MAX_NOTIFICATIONS_PER_DAY - notified_today

        created: list[dict[str, Any]] = []

        # 1. text_milestone：合并同日里程碑为一条文本（100%）
        if remaining_notify > 0:
            names = [self._milestone_label(u) for u in unlocked]
            merged = "、".join(names)
            text = f"🎉 解锁新里程碑：{merged}！继续加油，向更高段位前进。"
            rec = await self._create_and_maybe_deliver(
                tenant_id, agent_id, user_id, evaluation_id,
                reward_type=REWARD_TEXT_MILESTONE, status="delivered",
                message=text, related_milestone_ids=[u["milestone_id"] for u in unlocked],
            )
            created.append(rec)
            remaining_notify -= 1

        # 2. 随机其它奖励类型（基于本次解锁，独立掷点）
        # voice: suggested only（不占通知预算）
        if self._roll(REWARD_VOICE):
            created.append(await self._create(
                tenant_id, agent_id, user_id, evaluation_id,
                reward_type=REWARD_VOICE, status="suggested",
                message="一段语音鼓励（首版仅记录建议，未实际生成语音）。",
                related_milestone_id=unlocked[0]["milestone_id"],
            ))
        # badge：占通知预算
        if remaining_notify > 0 and self._roll(REWARD_BADGE):
            rec = await self._create(
                tenant_id, agent_id, user_id, evaluation_id,
                reward_type=REWARD_BADGE, status="delivered",
                message=f"获得徽章：{self._milestone_label(unlocked[0])}",
                related_milestone_id=unlocked[0]["milestone_id"],
            )
            created.append(rec)
            remaining_notify -= 1
        # red_packet_reminder：管理员提醒，每日最多 1，不占通知预算上限（spec 单独 1/天）
        if self._roll(REWARD_RED_PACKET):
            rp_today = await self._count_today_type(
                tenant_id, agent_id, user_id, evaluation_date, REWARD_RED_PACKET
            )
            if rp_today < MAX_RED_PACKET_PER_DAY:
                created.append(await self._create(
                    tenant_id, agent_id, user_id, evaluation_id,
                    reward_type=REWARD_RED_PACKET, status="suggested",
                    message="建议发放小额红包奖励，待管理员处理。",
                    related_milestone_id=unlocked[0]["milestone_id"],
                ))

        await self.db.flush()
        return created

    # ------------------------------------------------------------------

    async def _create_and_maybe_deliver(
        self, tenant_id, agent_id, user_id, evaluation_id, *,
        reward_type, status, message, related_milestone_ids,
    ):
        """创建奖励并尝试派发；派发失败记 failed。"""
        delivered_at = None
        delivery_error = None
        final_status = status
        if status == "delivered" and self._sender is not None:
            try:
                result = self._sender(user_id, message)
                if hasattr(result, "__await__"):
                    await result
                delivered_at = datetime.now(timezone.utc).isoformat()
            except Exception as e:  # noqa: BLE001
                delivery_error = {"reason": f"{type(e).__name__}: {e}"}
                final_status = "failed"
                logger.warning("Reward delivery failed (user=%s): %s", user_id, e)
        row = CoachReward(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            reward_type=reward_type, status=final_status, message=message,
            related_milestone_id=(related_milestone_ids[0] if related_milestone_ids else None),
            source_evaluation_id=evaluation_id,
            delivery_channel="dingtalk_single" if self._sender else None,
            delivery_target=user_id,
            delivered_at=delivered_at,
            error_json=__import__("json").dumps(delivery_error, ensure_ascii=False) if delivery_error else None,
        )
        self.db.add(row)
        await self.db.flush()
        return self._to_dict(row)

    async def _create(self, tenant_id, agent_id, user_id, evaluation_id, *,
                      reward_type, status, message, related_milestone_id=None):
        row = CoachReward(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            reward_type=reward_type, status=status, message=message,
            related_milestone_id=related_milestone_id,
            source_evaluation_id=evaluation_id,
        )
        self.db.add(row)
        await self.db.flush()
        return self._to_dict(row)

    def _roll(self, reward_type: str) -> bool:
        p = REWARD_PROBABILITIES.get(reward_type, 0.0)
        return self._rng() < p

    async def _count_today(
        self, tenant_id, agent_id, user_id, evaluation_date, *, exclude_types: Iterable[str] = ()
    ):
        """当日已创建的、计入通知预算的奖励数。

        ``evaluation_date`` 是评估业务日期；奖励的 ``created_at`` 是实际 UTC 时间戳。
        二者可能差一天（23:00 评估昨日）。为保证"每用户每天 N 条"的体验稳定，
        这里按实际 UTC 日期统计当前自然日已发的奖励。
        """
        today_prefix = datetime.now(timezone.utc).date().isoformat()
        conds = [
            CoachReward.tenant_id == tenant_id,
            CoachReward.agent_id == agent_id,
            CoachReward.user_id == user_id,
            CoachReward.created_at.like(f"{today_prefix}%"),
        ]
        stmt = select(func.count()).select_from(CoachReward)
        if exclude_types:
            conds.append(CoachReward.reward_type.notin_(list(exclude_types)))
        total = (await self.db.execute(stmt.where(*conds))).scalar()
        return int(total or 0)

    async def _count_today_type(
        self, tenant_id, agent_id, user_id, evaluation_date, reward_type
    ):
        today_prefix = datetime.now(timezone.utc).date().isoformat()
        total = (
            await self.db.execute(
                select(func.count()).select_from(CoachReward).where(
                    CoachReward.tenant_id == tenant_id,
                    CoachReward.agent_id == agent_id,
                    CoachReward.user_id == user_id,
                    CoachReward.reward_type == reward_type,
                    CoachReward.created_at.like(f"{today_prefix}%"),
                )
            )
        ).scalar()
        return int(total or 0)

    @staticmethod
    def _milestone_label(u: dict[str, Any]) -> str:
        dim = u.get("dimension")
        thr = u.get("threshold")
        if dim:
            return f"{dim}@{thr}"
        return f"all@{thr}"

    @staticmethod
    def _to_dict(row: CoachReward) -> dict[str, Any]:
        return {
            "id": row.id, "reward_type": row.reward_type, "status": row.status,
            "message": row.message, "related_milestone_id": row.related_milestone_id,
            "delivered_at": row.delivered_at,
        }


def _default_rng() -> float:
    import random
    return random.random()
