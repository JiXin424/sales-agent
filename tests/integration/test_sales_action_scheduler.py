"""销售动作调度器端到端集成测试（真实 DB + Fake sender，无真实网络/LLM）。

覆盖：
- 到期 one_time 提醒 → 投递成功（exactly-one）；
- failed + next_attempt_at 过期 → 在 max_attempts 内被重新认领并投递；
- failed + attempts >= max_attempts → 不再认领（dead-letter）；
- 投递失败 → attempts 递增、next_attempt_at 写入；
- digest 幂等创建 + 投递（list 渲染）。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sales_agent.models.sales_action import (
    SalesActionCard,
    SalesActionDelivery,
    SalesActionReminder,
)
from sales_agent.services.sales_actions.card_renderer import (
    build_digest_idempotency_key,
)
from sales_agent.services.sales_actions.repository import SalesActionRepository
from sales_agent.services.sales_actions.scheduler import (
    run_sales_action_scheduler_once,
)

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test",
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fake sender — captures sends/updates, never hits network
# ---------------------------------------------------------------------------


class FakeSender:
    def __init__(self, *, fail: bool = False):
        self.sends: list[tuple[str, str, str, str]] = []  # (user, title, md, out_track_id)
        self.updates: list[tuple[str, str]] = []
        self.finalizes: list[tuple[str, str]] = []  # (out_track_id, content)
        self._fail = fail
        self._n = 0

    async def send_markdown_card(self, dingtalk_user_id, title, markdown_text):
        if self._fail:
            raise RuntimeError("fake send failure")
        self._n += 1
        out = f"fake_out_{self._n}"
        self.sends.append((dingtalk_user_id, title, markdown_text, out))
        return out

    async def streaming_finalize(self, out_track_id, content, *, key="content"):
        # 关闭流式卡片"生成中"指示器；缺此帧卡片会一直"加载中"
        self.finalizes.append((out_track_id, content))

    async def update_card(self, out_track_id, markdown_text):
        self.updates.append((out_track_id, markdown_text))


# ---------------------------------------------------------------------------
# session factory fixture (schema create/drop; commits visible across sessions)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory():
    from sales_agent.core.database import Base

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


async def _seed_card(
    factory,
    *,
    tenant_id="t1",
    agent_id="a1",
    user_id="u1",
    dingtalk_user_id="du1",
    title="给张总回电话",
    customer_name="张总",
    action_type="call_back",
    status="pending",
    scheduled_at=datetime(2026, 7, 10, 1, 0, tzinfo=UTC),  # 09:00 Shanghai
):
    async with factory() as db:
        card = SalesActionCard(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            channel="dingtalk",
            dingtalk_user_id=dingtalk_user_id,
            conversation_id="conv1",
            source_kind="explicit_user",
            title=title,
            customer_name=customer_name,
            action_type=action_type,
            scheduled_at=scheduled_at,
            timezone="Asia/Shanghai",
            status=status,
        )
        db.add(card)
        await db.flush()
        await db.commit()
        return card.id


async def _seed_reminder(
    factory,
    *,
    action_id,
    tenant_id="t1",
    agent_id="a1",
    user_id="u1",
    remind_at=datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    reminder_type="one_time",
    status="scheduled",
    attempts=0,
    next_attempt_at=None,
    idempotency_key=None,
):
    async with factory() as db:
        r = SalesActionReminder(
            action_id=action_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            remind_at=remind_at,
            reminder_type=reminder_type,
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            idempotency_key=idempotency_key or f"seed:{action_id}:{reminder_type}:{status}",
        )
        db.add(r)
        await db.flush()
        await db.commit()
        return r.id


# ---------------------------------------------------------------------------
# Scenario 1: due one_time reminder delivered exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_reminder_delivered_once(session_factory):
    cid = await _seed_card(session_factory)
    rid = await _seed_reminder(session_factory, action_id=cid)

    sender = FakeSender()
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)  # past remind_at

    result = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="99:99", evening_time="99:99",  # disable digests
    )

    assert len(sender.sends) == 1
    user, title, md, out = sender.sends[0]
    assert user == "du1"
    assert "给张总回电话" in md
    assert "张总" in md
    assert title  # non-empty card title
    assert result.delivered == 1
    # 必须发流式结束帧关闭"生成中/加载中",且内容/outTrackId 与创建一致，
    # 否则钉钉卡片会一直转圈不出内容（回归守护）。
    assert sender.finalizes == [(out, md)]
    assert result.failed == 0

    # reminder flipped to delivered, delivery row recorded with card_instance_id
    async with session_factory() as db:
        reminder = (
            await db.execute(select(SalesActionReminder).where(SalesActionReminder.id == rid))
        ).scalar_one()
        assert reminder.status == "delivered"
        deliveries = (await db.execute(select(SalesActionDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == "success"
        assert deliveries[0].card_instance_id == out
        assert deliveries[0].action_id == cid


@pytest.mark.asyncio
async def test_second_run_does_not_redeliver(session_factory):
    cid = await _seed_card(session_factory)
    await _seed_reminder(session_factory, action_id=cid)
    sender = FakeSender()
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)
    await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now, morning_time="99:99", evening_time="99:99"
    )
    # second scan: nothing due anymore
    await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now, morning_time="99:99", evening_time="99:99"
    )
    assert len(sender.sends) == 1


# ---------------------------------------------------------------------------
# Scenario 2: failed reminder with next_attempt_at in the past is reclaimed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_reminder_reclaimed_within_max_attempts(session_factory):
    cid = await _seed_card(session_factory)
    rid = await _seed_reminder(
        session_factory,
        action_id=cid,
        status="failed",
        attempts=1,
        next_attempt_at=datetime(2026, 7, 10, 0, 50, tzinfo=UTC),  # past
    )

    sender = FakeSender()
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)
    await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="99:99", evening_time="99:99", max_attempts=5,
    )

    assert len(sender.sends) == 1
    async with session_factory() as db:
        reminder = (
            await db.execute(select(SalesActionReminder).where(SalesActionReminder.id == rid))
        ).scalar_one()
        assert reminder.status == "delivered"


@pytest.mark.asyncio
async def test_failed_reminder_past_max_attempts_is_dead_lettered(session_factory):
    cid = await _seed_card(session_factory)
    await _seed_reminder(
        session_factory,
        action_id=cid,
        status="failed",
        attempts=5,  # >= max_attempts
        next_attempt_at=datetime(2026, 7, 10, 0, 50, tzinfo=UTC),
    )
    sender = FakeSender()
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)
    await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="99:99", evening_time="99:99", max_attempts=5,
    )
    assert len(sender.sends) == 0  # not reclaimed


# ---------------------------------------------------------------------------
# Scenario 3: delivery failure increments attempts + schedules retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivery_failure_records_failure(session_factory):
    cid = await _seed_card(session_factory)
    rid = await _seed_reminder(session_factory, action_id=cid)

    sender = FakeSender(fail=True)
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)
    result = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="99:99", evening_time="99:99",
    )
    assert result.failed == 1
    assert result.delivered == 0

    async with session_factory() as db:
        reminder = (
            await db.execute(select(SalesActionReminder).where(SalesActionReminder.id == rid))
        ).scalar_one()
        assert reminder.status == "failed"
        assert reminder.attempts == 1
        assert reminder.last_error is not None
        assert reminder.next_attempt_at is not None
        assert reminder.next_attempt_at > now


# ---------------------------------------------------------------------------
# Scenario 4: morning digest created idempotently, then delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_morning_digest_created_and_delivered(session_factory):
    # one pending action for the scope
    cid = await _seed_card(session_factory)
    # now at 09:05 Shanghai (01:05 UTC) — within morning window
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)

    sender = FakeSender()
    # First run: creates the morning_digest reminder; the just-created reminder
    # is NOT yet due in this same claim (claim ran before creation), so it will
    # be delivered on the next run.
    r1 = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="09:00", evening_time="99:99", timezone_name="Asia/Shanghai",
        grace_hours=2,
    )
    assert r1.digests_created == 1

    # second run: digest reminder is now due and gets delivered as a digest card
    r2 = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now + timedelta(seconds=1),
        morning_time="09:00", evening_time="99:99", timezone_name="Asia/Shanghai",
        grace_hours=2,
    )
    assert r2.delivered == 1
    assert r2.digests_created == 0  # idempotent — not recreated

    # the delivered card content references the pending action title
    digest_send = [s for s in sender.sends if "给张总回电话" in s[2]]
    assert len(digest_send) >= 1

    # exactly one morning_digest reminder exists for this scope+date
    async with session_factory() as db:
        digests = (
            await db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.reminder_type == "morning_digest"
                )
            )
        ).scalars().all()
        assert len(digests) == 1
        assert digests[0].status == "delivered"


@pytest.mark.asyncio
async def test_digest_not_created_outside_window(session_factory):
    cid = await _seed_card(session_factory)
    # 23:00 local -> outside both windows
    now_local_23 = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)  # 23:00 Shanghai
    sender = FakeSender()
    r = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now_local_23,
        morning_time="09:00", evening_time="18:30", timezone_name="Asia/Shanghai",
        grace_hours=2,
    )
    assert r.digests_created == 0


# ---------------------------------------------------------------------------
# Scenario 5 (I-1 regression): a concurrent digest-insert collision is absorbed
# by the SAVEPOINT and never rolls back deliveries or raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_collision_absorbed_by_savepoint(session_factory, monkeypatch):
    """Two workers race past the pre-check; the loser's insert hits the
    ``uq_sales_action_reminders_idempotency`` unique constraint. The SAVEPOINT
    in ``_create_due_digests`` must catch the ``IntegrityError`` so the run does
    NOT raise, does NOT duplicate the digest, and does NOT roll back deliveries.
    """
    cid = await _seed_card(session_factory)  # scope t1/a1/u1 has a pending action
    now = datetime(2026, 7, 10, 1, 5, tzinfo=UTC)  # 09:05 Shanghai - in morning window
    today = date(2026, 7, 10)
    key = build_digest_idempotency_key("morning_digest", "t1", "a1", "u1", today)
    assert key == "morning_digest:t1:a1:u1:2026-07-10"

    # Simulate a concurrent worker that already committed this scope+date digest.
    await _seed_reminder(
        session_factory,
        action_id=None,
        reminder_type="morning_digest",
        status="scheduled",
        remind_at=now,
        idempotency_key=key,
    )

    # Force the pre-check to "not found" (False) so the code proceeds to the
    # insert and collides with the pre-seeded row -> exercises the savepoint.
    async def _precheck_misses(self, idempotency_key):  # noqa: ANN001
        return False

    monkeypatch.setattr(
        SalesActionRepository, "reminder_idempotency_key_exists", _precheck_misses
    )

    sender = FakeSender()
    # Must NOT raise; deliveries (none due here) and the pre-seeded digest survive.
    result = await run_sales_action_scheduler_once(
        session_factory, lambda: sender, now,
        morning_time="09:00", evening_time="99:99", timezone_name="Asia/Shanghai",
        grace_hours=2,
    )

    assert result.digests_created == 0  # collision absorbed, not counted
    async with session_factory() as db:
        digests = (
            await db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.reminder_type == "morning_digest"
                )
            )
        ).scalars().all()
        assert len(digests) == 1  # the pre-seeded one - no duplicate created
        assert digests[0].idempotency_key == key
