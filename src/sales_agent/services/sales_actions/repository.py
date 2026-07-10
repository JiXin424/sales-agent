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

from sqlalchemy import and_, or_, select
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
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        for_update: bool = False,
    ) -> SalesActionCard | None:
        stmt = select(SalesActionCard).where(
            SalesActionCard.id == action_id,
            SalesActionCard.tenant_id == scope.tenant_id,
            SalesActionCard.agent_id == scope.agent_id,
            SalesActionCard.user_id == scope.user_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

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
        success_criteria: str | None = None,
        pursuit_goal: str | None = None,
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
            success_criteria=success_criteria,
            pursuit_goal=pursuit_goal,
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

    async def _transition_to_terminal(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        new_status: str,
        event_type: str,
        reason_code: str,
        event_id: str | None,
    ) -> ActionStateResult:
        """Shared terminal-transition for complete/cancel.

        - missing / cross-scope → ``not_found``；
        - already terminal → 幂等 no-op（``already_done``/``already_terminal``），
          不重复写事件；
        - 否则翻转为 ``new_status``、取消在途提醒、写一条事件。
        """
        # Lock the card row so two concurrent terminal transitions serialize:
        # the second waits, re-reads the now-terminal status, and returns
        # already_done/already_terminal instead of writing a duplicate event.
        card = await self._get_scoped_card(scope, action_id, for_update=True)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")

        if card.status in TERMINAL_ACTION_STATUSES:
            return ActionStateResult(
                action_id=action_id,
                status=card.status,
                reason_code="already_done" if card.status == "done" else "already_terminal",
            )

        card.status = new_status
        await self._cancel_active_reminders(scope, action_id)
        self.db.add(
            self._event(
                scope,
                action_id=action_id,
                event_type=event_type,
                payload={"event_id": event_id} if event_id else {},
            )
        )
        await self.db.flush()
        return ActionStateResult(action_id=action_id, status=new_status, reason_code=reason_code)

    async def complete_action(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        event_id: str | None = None,
    ) -> ActionStateResult:
        """把动作置为终态 ``done`` 并取消在途提醒；幂等。"""
        return await self._transition_to_terminal(
            scope, action_id,
            new_status="done", event_type="action_completed",
            reason_code="completed", event_id=event_id,
        )

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
        return await self._transition_to_terminal(
            scope, action_id,
            new_status="cancelled", event_type="action_cancelled",
            reason_code="cancelled", event_id=event_id,
        )

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
        """推迟动作：取消所有在途提醒，再创建一条 ``snooze`` 提醒。

        - 缺失/跨作用域 → ``ActionStateResult(not_found)``；
        - 已终态 → ``ActionStateResult(already_terminal)``；
        - 同一 ``(action_id, event_id, new_time)`` 重复请求 → 幂等返回
          ``ActionStateResult(status="snoozed", reason_code="already_snoozed")``
          （按 idempotency_key 去重，不抛 IntegrityError，不重复插入提醒）。
        """
        card = await self._get_scoped_card(scope, action_id)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")

        if card.status in TERMINAL_ACTION_STATUSES:
            return ActionStateResult(
                action_id=action_id,
                status=card.status,
                reason_code="already_terminal",
            )

        key = snooze_idempotency_key(action_id, event_id, new_time)

        # Idempotent dedup (mirrors complete_action's ``already_done``): a repeat
        # snooze with the same (action_id, event_id, new_time) — exactly what
        # DingTalk at-least-once button delivery produces — returns a clean
        # result instead of crashing on the unique idempotency_key constraint.
        # The unique constraint remains as the hard backstop for true concurrency.
        existing = (
            await self.db.execute(
                select(SalesActionReminder).where(SalesActionReminder.idempotency_key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return ActionStateResult(
                action_id=action_id, status="snoozed", reason_code="already_snoozed"
            )

        # Cancel prior in-flight reminders so only the new time fires —
        # otherwise the user gets pinged at the time they just deferred.
        await self._cancel_active_reminders(scope, action_id)

        reminder = SalesActionReminder(
            action_id=action_id,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            remind_at=new_time,
            reminder_type="snooze",
            status="scheduled",
            attempts=0,
            idempotency_key=key,
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
    # ops visibility (agent-scoped reads for the operations API/console)
    # ------------------------------------------------------------------
    # 业务路径按 (tenant, agent, user) 三元组隔离；运维视角是「该 Agent 下所有
    # 用户的任务」，故这里按 (tenant, agent) 聚合，user_id 仅作为可选过滤项。

    async def list_actions_for_agent(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        status: str | None = None,
        action_type: str | None = None,
        user_id: str | None = None,
        scheduled_from: datetime | None = None,
        scheduled_to: datetime | None = None,
    ) -> list[SalesActionCard]:
        """按 Agent 维度列出动作卡片（跨用户，可选过滤）。"""
        stmt = (
            select(SalesActionCard)
            .where(
                SalesActionCard.tenant_id == tenant_id,
                SalesActionCard.agent_id == agent_id,
            )
            .order_by(SalesActionCard.scheduled_at.desc())
        )
        if status is not None:
            stmt = stmt.where(SalesActionCard.status == status)
        if action_type is not None:
            stmt = stmt.where(SalesActionCard.action_type == action_type)
        if user_id is not None:
            stmt = stmt.where(SalesActionCard.user_id == user_id)
        if scheduled_from is not None:
            stmt = stmt.where(SalesActionCard.scheduled_at >= scheduled_from)
        if scheduled_to is not None:
            stmt = stmt.where(SalesActionCard.scheduled_at <= scheduled_to)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_action_detail(
        self,
        tenant_id: str,
        agent_id: str,
        action_id: str,
    ) -> dict | None:
        """取单张卡片及其提醒/投递/事件（运维详情抽屉用）。

        按 (tenant, agent, action_id) 定位；action_id 在 tenant 内唯一，跨租户
        返回 None。
        """
        card = (
            await self.db.execute(
                select(SalesActionCard).where(
                    SalesActionCard.id == action_id,
                    SalesActionCard.tenant_id == tenant_id,
                    SalesActionCard.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()
        if card is None:
            return None
        reminders = list(
            (
                await self.db.execute(
                    select(SalesActionReminder)
                    .where(
                        SalesActionReminder.action_id == action_id,
                        SalesActionReminder.tenant_id == tenant_id,
                        SalesActionReminder.agent_id == agent_id,
                    )
                    .order_by(SalesActionReminder.remind_at.asc())
                )
            ).scalars().all()
        )
        deliveries = list(
            (
                await self.db.execute(
                    select(SalesActionDelivery)
                    .where(
                        SalesActionDelivery.action_id == action_id,
                        SalesActionDelivery.tenant_id == tenant_id,
                        SalesActionDelivery.agent_id == agent_id,
                    )
                    .order_by(SalesActionDelivery.created_at.desc())
                )
            ).scalars().all()
        )
        events = list(
            (
                await self.db.execute(
                    select(SalesActionEvent)
                    .where(
                        SalesActionEvent.action_id == action_id,
                        SalesActionEvent.tenant_id == tenant_id,
                        SalesActionEvent.agent_id == agent_id,
                    )
                    .order_by(SalesActionEvent.created_at.desc())
                )
            ).scalars().all()
        )
        return {"card": card, "reminders": reminders, "deliveries": deliveries, "events": events}

    async def list_reminders_for_agent(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        status: str | None = None,
        user_id: str | None = None,
    ) -> list[SalesActionReminder]:
        """按 Agent 维度列出提醒（跨用户，可按 status/user 过滤）。"""
        stmt = (
            select(SalesActionReminder)
            .where(
                SalesActionReminder.tenant_id == tenant_id,
                SalesActionReminder.agent_id == agent_id,
            )
            .order_by(SalesActionReminder.remind_at.desc())
        )
        if status is not None:
            stmt = stmt.where(SalesActionReminder.status == status)
        if user_id is not None:
            stmt = stmt.where(SalesActionReminder.user_id == user_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_deliveries_for_agent(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        status: str | None = None,
        user_id: str | None = None,
    ) -> list[SalesActionDelivery]:
        """按 Agent 维度列出投递记录（跨用户，可按 status/user 过滤）。"""
        stmt = (
            select(SalesActionDelivery)
            .where(
                SalesActionDelivery.tenant_id == tenant_id,
                SalesActionDelivery.agent_id == agent_id,
            )
            .order_by(SalesActionDelivery.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(SalesActionDelivery.status == status)
        if user_id is not None:
            stmt = stmt.where(SalesActionDelivery.user_id == user_id)
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # claim_due_reminders  (FOR UPDATE SKIP LOCKED)
    # ------------------------------------------------------------------

    async def claim_due_reminders(
        self,
        *,
        now: datetime,
        limit: int = 50,
        max_attempts: int = 5,
    ) -> list[SalesActionReminder]:
        """认领到期提醒，翻转为 ``sending``。

        一次 ``FOR UPDATE SKIP LOCKED`` 同时覆盖：

        - **到期未投递**：``status=="scheduled"`` 且 ``remind_at<=now``；
        - **失败可重试**：``status=="failed"`` 且 ``next_attempt_at<=now``
          且 ``attempts < max_attempts``（失败提醒原先一次失败即静默死亡，
          Task 5 修复——见 task-5-context §MUST-DO repository extension）。

        ``attempts >= max_attempts`` 的失败提醒不再被认领（dead-letter）。
        跨租户全局认领（调度器视角）。同一事务内二次调用看到的是已翻转
        的 ``sending`` 行，因此返回 ``[]``。
        """
        rows = (
            await self.db.execute(
                select(SalesActionReminder)
                .where(
                    or_(
                        and_(
                            SalesActionReminder.status == "scheduled",
                            SalesActionReminder.remind_at <= now,
                        ),
                        and_(
                            SalesActionReminder.status == "failed",
                            SalesActionReminder.next_attempt_at.is_not(None),
                            SalesActionReminder.next_attempt_at <= now,
                            SalesActionReminder.attempts < max_attempts,
                        ),
                    )
                )
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

    async def get_card(
        self,
        scope: SalesActionScope,
        action_id: str,
    ) -> SalesActionCard | None:
        """Get a single action card by scoped ID (read-only)."""
        return await self._get_scoped_card(scope, action_id)

    # ------------------------------------------------------------------
    # observe
    # ------------------------------------------------------------------

    async def write_outcome(
        self,
        scope: SalesActionScope,
        action_id: str,
        *,
        outcome_tag: str,
        outcome_note: str,
        met_signal: bool,
        event_id: str | None = None,
    ) -> ActionStateResult:
        """Write Observe result to a card. Idempotent: no-op if already written."""
        card = await self._get_scoped_card(scope, action_id, for_update=True)
        if card is None:
            return ActionStateResult(action_id=action_id, status="not_found", reason_code="action_not_found")
        if card.outcome_tag is not None:
            return ActionStateResult(
                action_id=action_id, status="already_observed", reason_code="outcome_already_captured"
            )
        card.outcome_tag = outcome_tag
        card.outcome_note = outcome_note
        card.outcome_met_signal = met_signal
        card.outcome_captured_at = datetime.now(timezone.utc)
        self.db.add(self._event(
            scope, action_id=action_id,
            event_type="action_observed",
            payload={"event_id": event_id, "outcome_tag": outcome_tag, "met_signal": met_signal} if event_id else {"outcome_tag": outcome_tag},
        ))
        await self.db.flush()
        return ActionStateResult(action_id=action_id, status="observed", reason_code="outcome_captured")

    async def get_card_for_reminder(
        self, reminder: SalesActionReminder
    ) -> SalesActionCard | None:
        """按 reminder.action_id + 同作用域加载关联卡片（用于取 dingtalk_user_id）。"""
        if reminder.action_id is None:
            return None
        stmt = select(SalesActionCard).where(
            SalesActionCard.id == reminder.action_id,
            SalesActionCard.tenant_id == reminder.tenant_id,
            SalesActionCard.agent_id == reminder.agent_id,
            SalesActionCard.user_id == reminder.user_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_cards_for_scope(
        self, scope: SalesActionScope, *, status: str = "pending"
    ) -> list[SalesActionCard]:
        """列出某作用域内指定状态的卡片（digest 渲染用）。"""
        stmt = (
            select(SalesActionCard)
            .where(
                SalesActionCard.tenant_id == scope.tenant_id,
                SalesActionCard.agent_id == scope.agent_id,
                SalesActionCard.user_id == scope.user_id,
                SalesActionCard.status == status,
            )
            .order_by(SalesActionCard.scheduled_at.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_active_action_scopes(self) -> list[SalesActionScope]:
        """枚举所有仍有 pending 动作的去重作用域（digest 创建用）。

        返回的 :class:`SalesActionScope` 携带 ``dingtalk_user_id``（取自卡片），
        供调度器随后投递 digest 卡片。
        """
        stmt = (
            select(
                SalesActionCard.tenant_id,
                SalesActionCard.agent_id,
                SalesActionCard.user_id,
                SalesActionCard.timezone,
                SalesActionCard.dingtalk_user_id,
            )
            .where(SalesActionCard.status == "pending")
            .distinct()
        )
        rows = (await self.db.execute(stmt)).all()
        scopes: list[SalesActionScope] = []
        seen: set[tuple] = set()
        for tenant_id, agent_id, user_id, _tz, dingtalk_user_id in rows:
            key = (tenant_id, agent_id, user_id, dingtalk_user_id)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(
                SalesActionScope(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    dingtalk_user_id=dingtalk_user_id,
                )
            )
        return scopes

    async def reminder_idempotency_key_exists(self, key: str) -> bool:
        """幂等键是否已存在（digest 去重预检，避免唯一约束冲突）。"""
        existing = (
            await self.db.execute(
                select(SalesActionReminder.id).where(
                    SalesActionReminder.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        return existing is not None

    def add_digest_reminder(
        self,
        scope: SalesActionScope,
        *,
        reminder_type: str,
        remind_at: datetime,
        idempotency_key: str,
    ) -> SalesActionReminder:
        """在当前会话中加入一条 digest 提醒（scheduled，立即到期）。

        幂等性由调用方通过 :meth:`reminder_idempotency_key_exists` 预检 +
        唯一约束兜底保证。``action_id=None`` —— digest 覆盖整个作用域的多个动作。
        """
        reminder = SalesActionReminder(
            action_id=None,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            remind_at=remind_at,
            reminder_type=reminder_type,
            status="scheduled",
            attempts=0,
            idempotency_key=idempotency_key,
        )
        self.db.add(reminder)
        return reminder


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_backoff(attempts: int) -> int:
    """有界指数退避（秒）；上界留给 Task 5 调参。"""
    return min(300, 2 ** max(1, attempts))
