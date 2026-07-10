"""销售动作卡片与提醒仓储的集成测试。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from sales_agent.models.sales_action import (
    SalesActionCard,
    SalesActionDelivery,
    SalesActionEvent,
    SalesActionReminder,
)
from sales_agent.services.sales_actions import SalesActionScope
from sales_agent.services.sales_actions.repository import (
    TERMINAL_ACTION_STATUSES,
    SalesActionRepository,
)


@pytest.mark.asyncio
async def test_sales_action_tables_and_indexes_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE tablename IN (
                   'sales_action_cards',
                   'sales_action_reminders',
                   'sales_action_deliveries',
                   'sales_action_events'
                 )
                """
            )
        )
    ).scalars().all()
    indexes = set(rows)
    assert "ix_sales_action_cards_scope_status" in indexes
    assert "ix_sales_action_cards_scheduled" in indexes
    assert "uq_sales_action_reminders_idempotency" in indexes
    assert "ix_sales_action_reminders_due" in indexes
    assert "ix_sales_action_deliveries_scope" in indexes
    assert "ix_sales_action_events_action" in indexes


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _scope(t="t1", a="a1", u="u1", du="du1") -> SalesActionScope:
    return SalesActionScope(tenant_id=t, agent_id=a, user_id=u, dingtalk_user_id=du)


async def _create_action(
    repo: SalesActionRepository,
    scope: SalesActionScope,
    *,
    title="给张总回电话",
    customer_name="张总",
    action_type="call_back",
    scheduled_at=datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc),
    source_event_id="evt1",
) -> SalesActionCard:
    return await repo.create_action(
        scope,
        title=title,
        customer_name=customer_name,
        action_type=action_type,
        scheduled_at=scheduled_at,
        timezone="Asia/Shanghai",
        conversation_id="conv1",
        topic_id=None,
        source_event_id=source_event_id,
        source_kind="explicit_user",
        context_snapshot={"source": "test"},
        agent_advice="先确认对方是否方便。",
    )


# ---------------------------------------------------------------------------
# Step 1: create + complete + duplicate complete is idempotent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_action_inserts_card_and_one_time_reminder(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)

    assert action.status == "pending"
    assert action.tenant_id == "t1"
    assert action.agent_id == "a1"
    assert action.user_id == "u1"

    reminders = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.action_id == action.id)
        )
    ).scalars().all()
    assert len(reminders) == 1
    assert reminders[0].reminder_type == "one_time"
    assert reminders[0].status == "scheduled"
    assert reminders[0].idempotency_key == (
        f"one_time:t1:a1:u1:{action.id}:2026-07-10T15:30:00+00:00"
    )

    events = (
        await db_session.execute(
            select(SalesActionEvent).where(SalesActionEvent.action_id == action.id)
        )
    ).scalars().all()
    assert any(e.event_type == "action_created" for e in events)


@pytest.mark.asyncio
async def test_create_complete_and_duplicate_complete_are_idempotent(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)
    assert action.status == "pending"

    first = await repo.complete_action(scope, action.id, event_id="click1")
    second = await repo.complete_action(scope, action.id, event_id="click1")
    assert first.status == "done"
    assert second.status == "done"
    assert second.reason_code == "already_done"

    # Action row flipped to terminal state.
    card = (
        await db_session.execute(
            select(SalesActionCard).where(SalesActionCard.id == action.id)
        )
    ).scalar_one()
    assert card.status == "done"
    assert card.status in TERMINAL_ACTION_STATUSES

    # Its scheduled reminder must be cancelled, not duplicated.
    reminders = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.action_id == action.id)
        )
    ).scalars().all()
    assert all(r.status == "cancelled" for r in reminders)

    # Only ONE completion event was written (idempotent — no duplicate events).
    complete_events = (
        await db_session.execute(
            select(SalesActionEvent)
            .where(SalesActionEvent.action_id == action.id)
            .where(SalesActionEvent.event_type == "action_completed")
        )
    ).scalars().all()
    assert len(complete_events) == 1


@pytest.mark.asyncio
async def test_complete_unknown_action_returns_not_found(db_session):
    repo = SalesActionRepository(db_session)
    res = await repo.complete_action(_scope(), "nope", event_id="e")
    assert res.status == "not_found"
    assert res.reason_code == "action_not_found"


# ---------------------------------------------------------------------------
# Step 2: claim_due_reminders marks sending once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_due_reminders_marks_sending_once(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(
        repo, scope, scheduled_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
    )

    reminders = await repo.claim_due_reminders(
        now=datetime(2026, 7, 10, 15, 1, tzinfo=timezone.utc), limit=10
    )
    assert len(reminders) == 1
    assert reminders[0].action_id == action.id
    assert reminders[0].status == "sending"

    again = await repo.claim_due_reminders(
        now=datetime(2026, 7, 10, 15, 1, tzinfo=timezone.utc), limit=10
    )
    assert again == []


@pytest.mark.asyncio
async def test_claim_due_reminders_skips_not_yet_due(db_session):
    repo = SalesActionRepository(db_session)
    await _create_action(
        repo, _scope(), scheduled_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
    )
    # before the remind_at
    reminders = await repo.claim_due_reminders(
        now=datetime(2026, 7, 10, 14, 59, tzinfo=timezone.utc), limit=10
    )
    assert reminders == []


# ---------------------------------------------------------------------------
# cancel / snooze / list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_action_sets_terminal_and_cancels_reminders(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)

    res = await repo.cancel_action(scope, action.id, event_id="cancel1")
    assert res.status == "cancelled"
    assert res.reason_code == "cancelled"

    card = (
        await db_session.execute(
            select(SalesActionCard).where(SalesActionCard.id == action.id)
        )
    ).scalar_one()
    assert card.status == "cancelled"

    reminders = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.action_id == action.id)
        )
    ).scalars().all()
    assert all(r.status == "cancelled" for r in reminders)


@pytest.mark.asyncio
async def test_cancel_terminal_action_is_idempotent(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)
    await repo.complete_action(scope, action.id, event_id="c1")

    res = await repo.cancel_action(scope, action.id, event_id="cancel1")
    assert res.status == "done"
    # already-terminal no-op reports the prior state consistently
    assert res.reason_code == "already_done"


@pytest.mark.asyncio
async def test_snooze_action_creates_new_snooze_reminder(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)

    new_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)
    reminder = await repo.snooze_action(
        scope, action.id, event_id="snooze1", new_time=new_time
    )
    assert reminder.reminder_type == "snooze"
    assert reminder.status == "scheduled"
    assert reminder.remind_at == new_time
    assert reminder.idempotency_key == (
        f"snooze:{action.id}:snooze1:2026-07-10T16:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_snooze_unknown_action_returns_not_found(db_session):
    repo = SalesActionRepository(db_session)
    res = await repo.snooze_action(
        _scope(), "nope", event_id="snooze1", new_time=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)
    )
    assert res.status == "not_found"


@pytest.mark.asyncio
async def test_snooze_cancels_original_reminder(db_session):
    """推迟后，原 one_time 提醒应被取消，只有新 snooze 提醒会触发。"""
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)

    new_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)
    await repo.snooze_action(scope, action.id, event_id="snooze1", new_time=new_time)

    reminders = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.action_id == action.id)
        )
    ).scalars().all()
    by_type = {r.reminder_type: r for r in reminders}
    assert by_type["one_time"].status == "cancelled"
    assert by_type["snooze"].status == "scheduled"
    # only the snooze reminder is claimable as due
    due = await repo.claim_due_reminders(now=new_time, limit=10)
    due_types = {r.reminder_type for r in due}
    assert "snooze" in due_types
    assert "one_time" not in due_types


@pytest.mark.asyncio
async def test_snooze_is_idempotent_on_duplicate(db_session):
    """同一 (action_id, event_id, new_time) 重复 snooze 幂等返回 already_snoozed，不抛错、不重复插入。"""
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)
    new_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)

    first = await repo.snooze_action(scope, action.id, event_id="snooze1", new_time=new_time)
    second = await repo.snooze_action(scope, action.id, event_id="snooze1", new_time=new_time)
    # duplicate is an idempotent no-op (no IntegrityError, no duplicate insert)
    assert second.status == "snoozed"
    assert second.reason_code == "already_snoozed"

    snooze_reminders = [
        r for r in (
            await db_session.execute(
                select(SalesActionReminder)
                .where(SalesActionReminder.action_id == action.id)
                .where(SalesActionReminder.reminder_type == "snooze")
            )
        ).scalars().all()
    ]
    assert len(snooze_reminders) == 1
    assert first.id == snooze_reminders[0].id


@pytest.mark.asyncio
async def test_list_actions_returns_scoped_cards(db_session):
    repo = SalesActionRepository(db_session)
    await _create_action(repo, _scope(), title="任务A")
    await _create_action(repo, _scope(), title="任务B")

    cards = await repo.list_actions(_scope())
    assert len(cards) == 2
    titles = {c.title for c in cards}
    assert titles == {"任务A", "任务B"}


@pytest.mark.asyncio
async def test_list_actions_cross_scope_returns_nothing(db_session):
    repo = SalesActionRepository(db_session)
    await _create_action(repo, _scope(t="t1"), title="任务A")
    # different tenant/agent/user
    other = await repo.list_actions(_scope(t="t2", a="a2", u="u2"))
    assert other == []


@pytest.mark.asyncio
async def test_cross_scope_complete_returns_not_found(db_session):
    repo = SalesActionRepository(db_session)
    action = await _create_action(repo, _scope(t="t1"))
    res = await repo.complete_action(_scope(t="t2"), action.id, event_id="e")
    assert res.status == "not_found"


# ---------------------------------------------------------------------------
# delivery success / failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_delivery_success_writes_delivery_and_event(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    action = await _create_action(repo, scope)
    reminders = await repo.claim_due_reminders(
        now=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc), limit=10
    )
    assert len(reminders) == 1
    rid = reminders[0].id

    await repo.record_delivery_success(
        scope,
        reminder_id=rid,
        action_id=action.id,
        dingtalk_message_id="msg1",
        rendered_text="提醒：给张总回电话",
    )

    reminder = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.id == rid)
        )
    ).scalar_one()
    assert reminder.status == "delivered"

    deliveries = (
        await db_session.execute(select(SalesActionDelivery))
    ).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].status == "success"
    assert deliveries[0].dingtalk_message_id == "msg1"


@pytest.mark.asyncio
async def test_record_delivery_failure_increments_attempts(db_session):
    repo = SalesActionRepository(db_session)
    scope = _scope()
    await _create_action(repo, scope)
    reminders = await repo.claim_due_reminders(
        now=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc), limit=10
    )
    rid = reminders[0].id

    await repo.record_delivery_failure(scope, reminder_id=rid, error="timeout")

    reminder = (
        await db_session.execute(
            select(SalesActionReminder).where(SalesActionReminder.id == rid)
        )
    ).scalar_one()
    assert reminder.status == "failed"
    assert reminder.attempts == 1
    assert reminder.last_error == "timeout"
    # backoff scheduled — bounded policy is Task 5, but next_attempt_at recorded
    assert reminder.next_attempt_at is not None
