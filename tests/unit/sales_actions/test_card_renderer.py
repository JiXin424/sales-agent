"""销售动作卡片渲染器单元测试（纯函数，无 DB/网络）。"""

from datetime import datetime, timezone

from sales_agent.services.sales_actions.card_renderer import (
    CardView,
    build_digest_idempotency_key,
    render_acknowledgement,
    render_due_reminder,
    render_evening_digest,
    render_morning_digest,
)


def _card(
    *,
    action_id="act_1",
    title="给张总回电话",
    customer_name="张总",
    action_type="call_back",
    scheduled_at=datetime(2026, 7, 10, 9, 30, tzinfo=timezone.utc),
    timezone="Asia/Shanghai",
) -> CardView:
    return CardView(
        action_id=action_id,
        title=title,
        customer_name=customer_name,
        action_type=action_type,
        scheduled_at=scheduled_at,
        timezone=timezone,
    )


# ---------------------------------------------------------------------------
# due reminder
# ---------------------------------------------------------------------------


def test_render_due_reminder_contains_title_time_customer():
    md = render_due_reminder(_card())
    assert "给张总回电话" in md
    assert "张总" in md
    # time is rendered (ISO or localized token present)
    assert "2026" in md
    # callback payload references the action, not raw context snapshot
    assert "act_1" in md


def test_render_due_reminder_omits_raw_sensitive_context():
    md = render_due_reminder(_card())
    # no json blob / context_snapshot leakage
    assert "{" not in md
    assert "context_snapshot" not in md
    assert "source_event_id" not in md


def test_render_due_reminder_handles_missing_customer():
    md = render_due_reminder(_card(customer_name=None))
    assert "给张总回电话" in md
    assert "act_1" in md


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------


def test_render_morning_digest_lists_pending_actions():
    cards = [
        _card(action_id="a1", title="任务一", customer_name="张总"),
        _card(action_id="a2", title="任务二", customer_name="李总"),
    ]
    md = render_morning_digest(cards)
    assert "任务一" in md
    assert "任务二" in md
    assert "a1" in md
    assert "a2" in md


def test_render_morning_digest_empty_is_friendly():
    md = render_morning_digest([])
    assert "暂无" in md or "没有" in md or "无待办" in md


def test_render_evening_digest_lists_pending_actions():
    cards = [_card(action_id="a1", title="跟进报价")]
    md = render_evening_digest(cards)
    assert "跟进报价" in md
    assert "a1" in md


# ---------------------------------------------------------------------------
# acknowledgement
# ---------------------------------------------------------------------------


def test_render_acknowledgement_complete():
    md = render_acknowledgement(action_id="act_1", status="done")
    assert "act_1" in md
    assert "完成" in md or "已" in md


def test_render_acknowledgement_cancel():
    md = render_acknowledgement(action_id="act_1", status="cancelled")
    assert "act_1" in md
    assert "取消" in md


def test_render_acknowledgement_snoozed_includes_new_time():
    md = render_acknowledgement(
        action_id="act_1",
        status="snoozed",
        new_time=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc),
    )
    assert "act_1" in md
    assert "2026" in md


# ---------------------------------------------------------------------------
# digest idempotency key (asserted verbatim per brief Step 1)
# ---------------------------------------------------------------------------


def test_digest_idempotency_key_format():
    from datetime import date

    assert build_digest_idempotency_key(
        "morning_digest", "t1", "a1", "u1", date(2026, 7, 10)
    ) == "morning_digest:t1:a1:u1:2026-07-10"


def test_digest_idempotency_key_evening():
    from datetime import date

    assert build_digest_idempotency_key(
        "evening_digest", "t1", "a1", "u1", date(2026, 7, 10)
    ) == "evening_digest:t1:a1:u1:2026-07-10"
