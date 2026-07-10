"""销售动作卡片与提醒的持久化状态机（repository）。

所有读写都按 ``tenant_id`` + ``agent_id`` + ``user_id``（来自
:class:`SalesActionScope`）隔离；跨作用域访问一律返回空/``not_found``。

状态机约定：

- ``SalesActionCard.status`` ∈ {``pending``, ``done``, ``cancelled``}；
  ``done``/``cancelled`` 为终态（:data:`TERMINAL_ACTION_STATUSES`）。
- ``SalesActionReminder.status`` ∈ {``scheduled``, ``sending``, ``delivered``,
  ``failed``, ``cancelled``}；其中 ``scheduled``/``sending`` 为可被取消/认领的
  在途态（:data:`ACTIVE_REMINDER_STATUSES`）。

``claim_due_reminders`` 使用 ``SELECT … FOR UPDATE SKIP LOCKED`` 认领到期提醒并
翻转为 ``sending``，镜像 :mod:`outbox_worker` 的认领范式。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.sales_action import (
    SalesActionCard,
    SalesActionDelivery,
    SalesActionEvent,
    SalesActionReminder,
)
from sales_agent.services.sales_actions.contracts import SalesActionScope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State-machine constants
# ---------------------------------------------------------------------------

#: A sales action ends in one of these; once reached it no longer accepts mutations.
TERMINAL_ACTION_STATUSES: frozenset[str] = frozenset({"done", "cancelled"})

#: Reminders that are still in-flight and therefore claimable / cancellable.
ACTIVE_REMINDER_STATUSES: frozenset[str] = frozenset({"scheduled", "sending"})

#: Reminder statuses that complete/cancel must neutralize.
REMINDER_CANCEL_TARGETS: frozenset[str] = ACTIVE_REMINDER_STATUSES


# ---------------------------------------------------------------------------
# Idempotency key builders
# ---------------------------------------------------------------------------

def _fmt_dt(dt: datetime) -> str:
    """Stable ISO-8601 rendering for idempotency keys (second precision).

    e.g. ``2026-07-10T15:30:00+00:00``. Naive datetimes are treated as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def one_time_idempotency_key(
    tenant_id: str,
    agent_id: str,
    user_id: str,
    action_id: str,
    scheduled_at: datetime,
) -> str:
    """``one_time:{tenant}:{agent}:{user}:{action_id}:{scheduled_at}``."""
    return f"one_time:{tenant_id}:{agent_id}:{user_id}:{action_id}:{_fmt_dt(scheduled_at)}"


def snooze_idempotency_key(
    action_id: str, event_id: str, new_time: datetime
) -> str:
    """``snooze:{action_id}:{event_id}:{new_time}``."""
    return f"snooze:{action_id}:{event_id}:{_fmt_dt(new_time)}"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionStateResult:
    """Result of a terminal state transition (complete/cancel/snooze-not-found)."""

    action_id: str
    status: str
    reason_code: str


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class SalesActionRepository:
    """销售动作卡片 + 提醒的状态机仓储。

    所有方法均在一个已开启的 :class:`AsyncSession` 上工作，使用 ``flush``
    把变更送入事务，由调用方（service / 测试 fixture）决定提交边界，
    与 :class:`AtomicMemoryRepository` 保持一致。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _event(
        self,
        scope: SalesActionScope,
        *,
        action_id: str | None,
        event_type: str,
        payload: dict | None = None,
    ) -> SalesActionEvent:
        return SalesActionEvent(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            action_id=action_id,
            event_type=event_type,
            event_payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )

    async def _get_scoped_card(
        self, scope: SalesActionScope, action_id: str
    ) -> SalesActionCard | None:
        return (
            await self.db.execute(
                select(SalesActionCard).where(
                    SalesActionCard.id == action_id,
                    SalesActionCard.tenant_id == scope.tenant_id,
                    SalesActionCard.agent_id == scope.agent_id,
                    SalesActionCard.user_id == scope.user_id,
                )
            )
        ).scalar_one_or_none()

    async def _cancel_active_reminders(
        self, scope: SalesActionScope, action_id: str
    ) -> int:
        rows = (
            await self.db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.tenant_id == scope.tenant_id,
                    SalesActionReminder.agent_id == scope.agent_id,
                    SalesActionReminder.user_id == scope.user_id,
                    SalesActionReminder.action_id == action_id,
                    SalesActionReminder.status.in_(REMINDER_CANCEL_TARGETS),
                )
            )
        ).scalars().all()
        for r in rows:
            r.status = "cancelled"
        return len(rows)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def create_action(
        self,
        scope: SalesActionScope,
        *,
        title: str,
        customer_name: str | None,
        action_type: str,
        scheduled_at: datetime,
        timezone: str,
        conversation_id: str,
        topic_id: str | None,
        source_event_id: str | None,
        source_kind: str,
        context_snapshot: dict,
        agent_advice: str,
    ) -> SalesActionCard:
        """创建一张 pending 动作卡片 + 一条 one_time 提醒。

        幂等键 ``one_time:{tenant}:{agent}:{user}:{action_id}:{scheduled_at}``
        保证同一动作不会被重复创建提醒。
        """
        card = SalesActionCard(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            dingtalk_user_id=scope.dingtalk_user_id,
            conversation_id=conversation_id,
            topic_id=topic_id,
            source_event_id=source_event_id,
            source_kind=source_kind,
            title=title,
            customer_name=customer_name,
            action_type=action_type,
            scheduled_at=scheduled_at,
            timezone=timezone,
            status="pending",
            context_snapshot_json=json.dumps(context_snapshot or {}, ensure_ascii=False),
            agent_advice=agent_advice or "",
        )
        self.db.add(card)
        await self.db.flush()

        key = one_time_idempotency_key(
            scope.tenant_id,
            scope.agent_id,
            scope.user_id,
            card.id,
            scheduled_at,
        )
        reminder = SalesActionReminder(
            action_id=card.id,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            remind_at=scheduled_at,
            reminder_type="one_time",
            status="scheduled",
            attempts=0,
            idempotency_key=key,
        )
        self.db.add(reminder)
        self.db.add(
            self._event(
                scope,
                action_id=card.id,
                event_type="action_created",
                payload={
                    "title": title,
                    "customer_name": customer_name,
                    "action_type": action_type,
                    "scheduled_at": _fmt_dt(scheduled_at),
                },
            )
        )
        await self.db.flush()
        return card

    # ------------------------------------------------------------------
    # complete
    # ------------------------------------------------------------------

    async def complete_action(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        event_id: str | None = None,
    ) -> ActionStateResult:
        """把动作置为终态 ``done`` 并取消在途提醒；幂等。"""
        card = await self._get_scoped_card(scope, action_id)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")

        # Already terminal → idempotent no-op (do not duplicate events).
        if card.status in TERMINAL_ACTION_STATUSES:
            return ActionStateResult(
                action_id=action_id,
                status=card.status,
                reason_code="already_done" if card.status == "done" else "already_terminal",
            )

        card.status = "done"
        await self._cancel_active_reminders(scope, action_id)
        self.db.add(
            self._event(
                scope,
                action_id=action_id,
                event_type="action_completed",
                payload={"event_id": event_id} if event_id else {},
            )
        )
        await self.db.flush()
        return ActionStateResult(action_id=action_id, status="done", reason_code="completed")

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    async def cancel_action(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        event_id: str | None = None,
    ) -> ActionStateResult:
        """把动作置为终态 ``cancelled`` 并取消在途提醒；幂等。"""
        card = await self._get_scoped_card(scope, action_id)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")

        if card.status in TERMINAL_ACTION_STATUSES:
            return ActionStateResult(
                action_id=action_id,
                status=card.status,
                reason_code="already_terminal",
            )

        card.status = "cancelled"
        await self._cancel_active_reminders(scope, action_id)
        self.db.add(
            self._event(
                scope,
                action_id=action_id,
                event_type="action_cancelled",
                payload={"event_id": event_id} if event_id else {},
            )
        )
        await self.db.flush()
        return ActionStateResult(action_id=action_id, status="cancelled", reason_code="cancelled")

    # ------------------------------------------------------------------
    # snooze
    # ------------------------------------------------------------------

    async def snooze_action(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        event_id: str,
        new_time: datetime,
    ) -> SalesActionReminder | ActionStateResult:
        """为动作创建一条新的 ``snooze`` 提醒（不改原提醒）。"""
        card = await self._get_scoped_card(scope, action_id)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")

        if card.status in TERMINAL_ACTION_STATUSES:
            return ActionStateResult(
                action_id=action_id,
                status=card.status,
                reason_code="already_terminal",
            )

        reminder = SalesActionReminder(
            action_id=action_id,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            remind_at=new_time,
            reminder_type="snooze",
            status="scheduled",
            attempts=0,
            idempotency_key=snooze_idempotency_key(action_id, event_id, new_time),
        )
        self.db.add(reminder)
        self.db.add(
            self._event(
                scope,
                action_id=action_id,
                event_type="action_snoozed",
                payload={"event_id": event_id, "new_time": _fmt_dt(new_time)},
            )
        )
        await self.db.flush()
        return reminder

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def list_actions(
        self,
        scope: SalesActionScope,
        *,
        status: str | None = None,
    ) -> list[SalesActionCard]:
        """列出作用域内的动作卡片（默认全部，可按 status 过滤）。"""
        stmt = (
            select(SalesActionCard)
            .where(
                SalesActionCard.tenant_id == scope.tenant_id,
                SalesActionCard.agent_id == scope.agent_id,
                SalesActionCard.user_id == scope.user_id,
            )
            .order_by(SalesActionCard.scheduled_at.asc())
        )
        if status is not None:
            stmt = stmt.where(SalesActionCard.status == status)
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # claim_due_reminders  (FOR UPDATE SKIP LOCKED)
    # ------------------------------------------------------------------

    async def claim_due_reminders(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[SalesActionReminder]:
        """认领所有到期且仍 ``scheduled`` 的提醒，翻转 为 ``sending``。

        跨租户全局认领（调度器视角）。使用 ``FOR UPDATE SKIP LOCKED``
        保证多 worker 并发不重复认领。同一事务内二次调用看到的是已翻转
        的 ``sending`` 行，因此返回 ``[]``。
        """
        rows = (
            await self.db.execute(
                select(SalesActionReminder)
                .where(SalesActionReminder.status == "scheduled")
                .where(SalesActionReminder.remind_at <= now)
                .order_by(SalesActionReminder.remind_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        for r in rows:
            r.status = "sending"
        await self.db.flush()
        return list(rows)

    # ------------------------------------------------------------------
    # delivery success / failure
    # ------------------------------------------------------------------

    async def record_delivery_success(
        self,
        scope: SalesActionScope,
        *,
        reminder_id: str,
        action_id: str | None,
        dingtalk_message_id: str | None = None,
        card_instance_id: str | None = None,
        rendered_text: str = "",
    ) -> SalesActionDelivery:
        """记录一次成功投递 + 事件，并把提醒置为 ``delivered``。"""
        reminder = await self._get_scoped_reminder(scope, reminder_id)
        delivery = SalesActionDelivery(
            action_id=action_id,
            reminder_id=reminder_id,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            delivery_type=reminder.reminder_type if reminder else "one_time",
            dingtalk_message_id=dingtalk_message_id,
            card_instance_id=card_instance_id,
            rendered_text=rendered_text,
            status="success",
        )
        self.db.add(delivery)
        self.db.add(
            self._event(
                scope,
                action_id=action_id,
                event_type="reminder_delivered",
                payload={"reminder_id": reminder_id, "message_id": dingtalk_message_id},
            )
        )
        if reminder is not None:
            reminder.status = "delivered"
        await self.db.flush()
        return delivery

    async def record_delivery_failure(
        self,
        scope: SalesActionScope,
        *,
        reminder_id: str,
        action_id: str | None = None,
        error: str,
    ) -> SalesActionDelivery:
        """记录一次失败投递 + 事件，递增 attempts，写入 last_error/next_attempt_at。

        退避上界策略属 Task 5；此处仅按 attempts 做有界指数退避并落库。
        """
        reminder = await self._get_scoped_reminder(scope, reminder_id)
        delivery = SalesActionDelivery(
            action_id=action_id or (reminder.action_id if reminder else None),
            reminder_id=reminder_id,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            delivery_type=reminder.reminder_type if reminder else "one_time",
            rendered_text="",
            status="failed",
            error=error,
        )
        self.db.add(delivery)
        self.db.add(
            self._event(
                scope,
                action_id=action_id or (reminder.action_id if reminder else None),
                event_type="reminder_delivery_failed",
                payload={"reminder_id": reminder_id, "error": error},
            )
        )
        if reminder is not None:
            reminder.attempts += 1
            reminder.last_error = error
            reminder.next_attempt_at = _now_utc() + timedelta(
                seconds=_bounded_backoff(reminder.attempts)
            )
            reminder.status = "failed"
        await self.db.flush()
        return delivery

    async def _get_scoped_reminder(
        self, scope: SalesActionScope, reminder_id: str
    ) -> SalesActionReminder | None:
        return (
            await self.db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.id == reminder_id,
                    SalesActionReminder.tenant_id == scope.tenant_id,
                    SalesActionReminder.agent_id == scope.agent_id,
                    SalesActionReminder.user_id == scope.user_id,
                )
            )
        ).scalar_one_or_none()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_backoff(attempts: int) -> int:
    """有界指数退避（秒）；上界留给 Task 5 调参。"""
    return min(300, 2 ** max(1, attempts))
