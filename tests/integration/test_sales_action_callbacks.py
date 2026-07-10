"""销售动作钉钉卡片按钮回调处理（集成测试：真实 DB + Fake sender）。

验证内部回调契约：complete / cancel / snooze 三种按钮动作经
``handle_sales_action_callback`` 后正确驱动仓储状态机并回渲染卡片。
不触碰真实钉钉网络与签名（路由层签名校验由 routes.py 既有范式覆盖）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sales_agent.integrations.dingtalk.sales_action_callbacks import (
    SalesActionCallbackRequest,
    handle_sales_action_callback,
)
from sales_agent.models.sales_action import SalesActionCard, SalesActionReminder

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test",
)

UTC = timezone.utc


class FakeSender:
    def __init__(self):
        self.sends: list[tuple[str, str, str, str]] = []
        self.updates: list[tuple[str, str]] = []
        self._n = 0

    async def send_markdown_card(self, dingtalk_user_id, title, markdown_text):
        self._n += 1
        out = f"fake_out_{self._n}"
        self.sends.append((dingtalk_user_id, title, markdown_text, out))
        return out

    async def update_card(self, out_track_id, markdown_text):
        self.updates.append((out_track_id, markdown_text))


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


async def _seed_pending_card(factory, *, dingtalk_user_id="du1", action_id=None):
    async with factory() as db:
        card = SalesActionCard(
            tenant_id="t1",
            agent_id="a1",
            user_id="u1",
            channel="dingtalk",
            dingtalk_user_id=dingtalk_user_id,
            conversation_id="conv1",
            source_kind="explicit_user",
            title="给张总回电话",
            customer_name="张总",
            action_type="call_back",
            scheduled_at=datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
            timezone="Asia/Shanghai",
            status="pending",
        )
        if action_id:
            card.id = action_id
        db.add(card)
        await db.flush()
        cid = card.id
        await db.commit()
        return cid


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_complete_flips_done_and_updates_card(session_factory):
    cid = await _seed_pending_card(session_factory)
    sender = FakeSender()
    req = SalesActionCallbackRequest(
        action_id=cid,
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        button="complete",
        event_id="click1",
        card_instance_id="out_xyz",
    )
    async with session_factory() as db:
        res = await handle_sales_action_callback(db, req, sender=sender)
        await db.commit()

    assert res.status == "done"
    assert len(sender.updates) == 1
    out_track_id, md = sender.updates[0]
    assert out_track_id == "out_xyz"
    assert cid in md

    async with session_factory() as db:
        card = (await db.execute(select(SalesActionCard).where(SalesActionCard.id == cid))).scalar_one()
        assert card.status == "done"


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_cancel_flips_cancelled(session_factory):
    cid = await _seed_pending_card(session_factory)
    sender = FakeSender()
    req = SalesActionCallbackRequest(
        action_id=cid, tenant_id="t1", agent_id="a1", user_id="u1",
        button="cancel", event_id="click2", card_instance_id="out_xyz",
    )
    async with session_factory() as db:
        res = await handle_sales_action_callback(db, req, sender=sender)
        await db.commit()
    assert res.status == "cancelled"
    assert len(sender.updates) == 1
    async with session_factory() as db:
        card = (await db.execute(select(SalesActionCard).where(SalesActionCard.id == cid))).scalar_one()
        assert card.status == "cancelled"


# ---------------------------------------------------------------------------
# snooze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_snooze_creates_new_reminder(session_factory):
    cid = await _seed_pending_card(session_factory)
    sender = FakeSender()
    new_time = datetime(2026, 7, 10, 3, 0, tzinfo=UTC)
    req = SalesActionCallbackRequest(
        action_id=cid, tenant_id="t1", agent_id="a1", user_id="u1",
        button="snooze", event_id="click3", card_instance_id="out_xyz",
        new_time=new_time,
    )
    async with session_factory() as db:
        res = await handle_sales_action_callback(db, req, sender=sender)
        await db.commit()
    assert res.status == "snoozed"
    assert len(sender.updates) == 1
    async with session_factory() as db:
        snooze_reminders = (
            await db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.action_id == cid,
                    SalesActionReminder.reminder_type == "snooze",
                )
            )
        ).scalars().all()
        assert len(snooze_reminders) == 1
        assert snooze_reminders[0].remind_at == new_time


# ---------------------------------------------------------------------------
# idempotency: duplicate complete is a no-op (no duplicate update)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_complete_is_idempotent(session_factory):
    cid = await _seed_pending_card(session_factory)
    sender = FakeSender()
    req = SalesActionCallbackRequest(
        action_id=cid, tenant_id="t1", agent_id="a1", user_id="u1",
        button="complete", event_id="click1", card_instance_id="out_xyz",
    )
    async with session_factory() as db:
        first = await handle_sales_action_callback(db, req, sender=sender)
        second = await handle_sales_action_callback(db, req, sender=sender)
        await db.commit()
    assert first.status == "done"
    assert second.status == "done"
    assert second.reason_code == "already_done"


@pytest.mark.asyncio
async def test_callback_snooze_is_idempotent(session_factory):
    """Duplicate snooze button event (same action+event+new_time) is a no-op.

    Locks the ``SalesActionReminder | ActionStateResult`` contract of
    ``snooze_action``: a second click returns ``already_snoozed`` and does NOT
    create a second snooze reminder.
    """
    cid = await _seed_pending_card(session_factory)
    sender = FakeSender()
    new_time = datetime(2026, 7, 10, 3, 0, tzinfo=UTC)
    req = SalesActionCallbackRequest(
        action_id=cid, tenant_id="t1", agent_id="a1", user_id="u1",
        button="snooze", event_id="click3", card_instance_id="out_xyz",
        new_time=new_time,
    )
    async with session_factory() as db:
        first = await handle_sales_action_callback(db, req, sender=sender)
        second = await handle_sales_action_callback(db, req, sender=sender)
        await db.commit()
    assert first.status == "snoozed"
    assert second.status == "snoozed"
    assert second.reason_code == "already_snoozed"
    # exactly one snooze reminder - the duplicate did not create a second
    async with session_factory() as db:
        snooze_reminders = (
            await db.execute(
                select(SalesActionReminder).where(
                    SalesActionReminder.action_id == cid,
                    SalesActionReminder.reminder_type == "snooze",
                )
            )
        ).scalars().all()
        assert len(snooze_reminders) == 1


# ---------------------------------------------------------------------------
# not_found / cross-scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_unknown_action_returns_not_found(session_factory):
    sender = FakeSender()
    req = SalesActionCallbackRequest(
        action_id="nope", tenant_id="t1", agent_id="a1", user_id="u1",
        button="complete", event_id="c", card_instance_id="out",
    )
    async with session_factory() as db:
        res = await handle_sales_action_callback(db, req, sender=sender)
    assert res.status == "not_found"
    assert len(sender.updates) == 0
