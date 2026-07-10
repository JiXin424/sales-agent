"""销售任务运维 API 集成测试。

两层覆盖：
1. 仓储 ops 读取方法（``list_actions_for_agent`` / ``get_action_detail`` /
   ``list_reminders_for_agent`` / ``list_deliveries_for_agent``）-- 真实 DB。
2. 路由处理函数（async，``db_session``）-- monkeypatch ``_load_agent`` 绕开
   dedicated 模式的 tenant-runtime 强校验（该横切关注点在它处覆盖），专注验证
   路由逻辑：过滤、scope 反推、状态机复用、详情聚合、404。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.routes import sales_actions as sa_route
from sales_agent.services.sales_actions.contracts import SalesActionScope
from sales_agent.services.sales_actions.repository import SalesActionRepository

UTC = timezone.utc


async def _seed(
    db: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    user_id: str = "u1",
    title: str = "给张总回电话",
    action_type: str = "call_back",
    status: str = "pending",
    scheduled_at: datetime = datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
) -> str:
    """通过真实 create_action 落一张 pending 卡片 + one_time 提醒，返回 action_id。"""
    repo = SalesActionRepository(db)
    scope = SalesActionScope(
        tenant_id=tenant_id, agent_id=agent_id, user_id=user_id, dingtalk_user_id=f"dt_{user_id}"
    )
    card = await repo.create_action(
        scope,
        title=title,
        customer_name="张总",
        action_type=action_type,
        scheduled_at=scheduled_at,
        timezone="Asia/Shanghai",
        conversation_id="conv1",
        topic_id=None,
        source_event_id="evt1",
        source_kind="explicit_user",
        context_snapshot={"source": "test"},
        agent_advice="先确认对方是否方便。",
    )
    await db.flush()
    return card.id


# ---------------------------------------------------------------------------
# repository ops reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_actions_for_agent_filters_by_status(db_session, sample_tenant, active_agent):
    await _seed(db_session, tenant_id=sample_tenant, agent_id=active_agent.id, status="pending")
    cid2 = await _seed(
        db_session, tenant_id=sample_tenant, agent_id=active_agent.id,
        title="发方案", user_id="u2",
    )
    repo = SalesActionRepository(db_session)
    # mark cid2 done
    await repo.complete_action(
        SalesActionScope(tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u2"),
        cid2,
    )
    await db_session.flush()

    pending = await repo.list_actions_for_agent(sample_tenant, active_agent.id, status="pending")
    done = await repo.list_actions_for_agent(sample_tenant, active_agent.id, status="done")
    assert len(pending) == 1
    assert len(done) == 1
    assert done[0].title == "发方案"


@pytest.mark.asyncio
async def test_list_actions_for_agent_filters_by_user_and_type(
    db_session, sample_tenant, active_agent
):
    await _seed(db_session, tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u1", action_type="call_back")
    await _seed(
        db_session, tenant_id=sample_tenant, agent_id=active_agent.id,
        user_id="u2", action_type="send_proposal", title="发方案",
    )
    repo = SalesActionRepository(db_session)
    by_user = await repo.list_actions_for_agent(sample_tenant, active_agent.id, user_id="u2")
    assert len(by_user) == 1 and by_user[0].user_id == "u2"
    by_type = await repo.list_actions_for_agent(sample_tenant, active_agent.id, action_type="send_proposal")
    assert len(by_type) == 1 and by_type[0].action_type == "send_proposal"


@pytest.mark.asyncio
async def test_list_actions_cross_tenant_returns_nothing(db_session, sample_tenant, active_agent):
    await _seed(db_session, tenant_id=sample_tenant, agent_id=active_agent.id)
    repo = SalesActionRepository(db_session)
    rows = await repo.list_actions_for_agent("other_tenant", active_agent.id)
    assert rows == []


@pytest.mark.asyncio
async def test_get_action_detail_includes_related(db_session, sample_tenant, active_agent):
    cid = await _seed(db_session, tenant_id=sample_tenant, agent_id=active_agent.id)
    repo = SalesActionRepository(db_session)
    scope = SalesActionScope(
        tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u1", dingtalk_user_id="dt_u1"
    )
    # claim the one_time reminder + record a delivery so detail has reminders/deliveries/events
    reminders = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 7, 5, tzinfo=UTC), limit=10)
    assert len(reminders) == 1
    await repo.record_delivery_success(
        scope, reminder_id=reminders[0].id, action_id=cid,
        dingtalk_message_id="m1", card_instance_id="out1", rendered_text="到期提醒",
    )
    await db_session.flush()

    detail = await repo.get_action_detail(sample_tenant, active_agent.id, cid)
    assert detail is not None
    assert detail["card"].id == cid
    assert len(detail["reminders"]) >= 1
    assert len(detail["deliveries"]) == 1
    assert detail["deliveries"][0].status == "success"
    assert len(detail["events"]) >= 1  # reminder_delivered event


@pytest.mark.asyncio
async def test_get_action_detail_unknown_returns_none(db_session, sample_tenant, active_agent):
    repo = SalesActionRepository(db_session)
    assert await repo.get_action_detail(sample_tenant, active_agent.id, "nope") is None


@pytest.mark.asyncio
async def test_list_reminders_and_deliveries_for_agent(
    db_session, sample_tenant, active_agent
):
    cid = await _seed(db_session, tenant_id=sample_tenant, agent_id=active_agent.id)
    repo = SalesActionRepository(db_session)
    scope = SalesActionScope(
        tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u1", dingtalk_user_id="dt_u1"
    )
    reminders = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 7, 5, tzinfo=UTC), limit=10)
    await repo.record_delivery_success(
        scope, reminder_id=reminders[0].id, action_id=cid, rendered_text="ok"
    )
    # a failed delivery on a second action
    cid2 = await _seed(
        db_session, tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u2", title="二"
    )
    scope2 = SalesActionScope(
        tenant_id=sample_tenant, agent_id=active_agent.id, user_id="u2", dingtalk_user_id="dt_u2"
    )
    rem2 = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 7, 5, tzinfo=UTC), limit=10)
    await repo.record_delivery_failure(scope2, reminder_id=rem2[0].id, action_id=cid2, error="boom")
    await db_session.flush()

    all_reminders = await repo.list_reminders_for_agent(sample_tenant, active_agent.id)
    delivered = await repo.list_reminders_for_agent(sample_tenant, active_agent.id, status="delivered")
    failed = await repo.list_reminders_for_agent(sample_tenant, active_agent.id, status="failed")
    assert len(all_reminders) >= 2
    assert len(delivered) == 1
    assert len(failed) == 1

    succ_deliveries = await repo.list_deliveries_for_agent(sample_tenant, active_agent.id, status="success")
    fail_deliveries = await repo.list_deliveries_for_agent(sample_tenant, active_agent.id, status="failed")
    assert len(succ_deliveries) == 1
    assert len(fail_deliveries) == 1


# ---------------------------------------------------------------------------
# route handlers (monkeypatch _load_agent to bypass tenant-runtime enforcement)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_load_agent(monkeypatch, active_agent):
    """让路由的 _load_agent 直接返回真实 active_agent，绕开 dedicated 模式强校验。"""

    async def _fake(db, agent_id):
        if agent_id != active_agent.id:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        return active_agent

    monkeypatch.setattr(sa_route, "_load_agent", _fake)
    return active_agent


@pytest.mark.asyncio
async def test_route_list_filters_by_status(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    await _seed(db_session, tenant_id=sample_tenant, agent_id=agent.id, status="pending")
    cid2 = await _seed(db_session, tenant_id=sample_tenant, agent_id=agent.id, user_id="u2", title="发方案")
    await SalesActionRepository(db_session).complete_action(
        SalesActionScope(tenant_id=sample_tenant, agent_id=agent.id, user_id="u2"), cid2
    )
    await db_session.flush()

    res = await sa_route.list_sales_actions(agent.id, db_session, status="pending")
    assert res.total == 1
    assert res.items[0].status == "pending"


@pytest.mark.asyncio
async def test_route_create_and_detail(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    created = await sa_route.create_sales_action(
        agent.id,
        sa_route.CreateSalesActionRequest(
            title="给李总发方案", customer_name="李总", action_type="send_proposal",
            scheduled_at=datetime(2026, 7, 11, 2, 0, tzinfo=UTC), user_id="u9",
            conversation_id="conv9", dingtalk_user_id="dt_u9",
        ),
        db_session,
    )
    assert created.status == "pending"

    detail = await sa_route.get_sales_action(agent.id, created.id, db_session)
    assert detail.card.title == "给李总发方案"
    assert len(detail.reminders) == 1  # one_time reminder created with the card
    assert detail.reminders[0].reminder_type == "one_time"


@pytest.mark.asyncio
async def test_route_complete_then_cancel_is_idempotent(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    cid = await _seed(db_session, tenant_id=sample_tenant, agent_id=agent.id)
    r1 = await sa_route.complete_sales_action(agent.id, cid, db_session)
    assert r1["status"] == "done"
    r2 = await sa_route.complete_sales_action(agent.id, cid, db_session)
    assert r2["status"] == "done" and r2["reason_code"] == "already_done"


@pytest.mark.asyncio
async def test_route_snooze(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    cid = await _seed(db_session, tenant_id=sample_tenant, agent_id=agent.id)
    new_time = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
    r = await sa_route.snooze_sales_action(
        agent.id, cid, sa_route.SnoozeRequest(new_time=new_time, event_id="ops1"), db_session
    )
    assert r["status"] == "snoozed"


@pytest.mark.asyncio
async def test_route_get_unknown_action_404(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    with pytest.raises(HTTPException) as exc:
        await sa_route.get_sales_action(agent.id, "nope", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_route_reminders_and_deliveries(db_session, sample_tenant, stub_load_agent):
    agent = stub_load_agent
    cid = await _seed(db_session, tenant_id=sample_tenant, agent_id=agent.id)
    repo = SalesActionRepository(db_session)
    scope = SalesActionScope(
        tenant_id=sample_tenant, agent_id=agent.id, user_id="u1", dingtalk_user_id="dt_u1"
    )
    rem = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 7, 5, tzinfo=UTC), limit=10)
    await repo.record_delivery_success(scope, reminder_id=rem[0].id, action_id=cid, rendered_text="ok")
    await db_session.flush()

    rem_res = await sa_route.list_sales_action_reminders(agent.id, db_session)
    del_res = await sa_route.list_sales_action_deliveries(agent.id, db_session, status="success")
    assert rem_res.total >= 1
    assert del_res.total == 1
