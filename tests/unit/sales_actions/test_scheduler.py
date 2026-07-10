"""销售动作调度器单元测试（纯函数部分：backoff + 幂等键 + 渲染分流）。

依赖 DB 的 claim-reclaims-failed / 端到端投递在
``tests/integration/test_sales_action_scheduler.py``。
"""

from datetime import date, datetime, timezone

import pytest

from sales_agent.services.sales_actions.scheduler import (
    build_digest_idempotency_key,
    compute_sales_action_backoff_seconds,
    scope_from_reminder,
    within_digest_window,
)


# ---------------------------------------------------------------------------
# backoff (brief Step 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attempts,expected",
    [
        (0, 2),    # max(1,0)=1 -> 2**1=2
        (1, 2),    # 2**1
        (2, 4),    # 2**2
        (3, 8),
        (4, 16),
        (8, 256),
        (9, 300),  # 2**9=512 -> capped at 300
        (20, 300),
    ],
)
def test_compute_backoff_seconds(attempts, expected):
    assert compute_sales_action_backoff_seconds(attempts) == expected


def test_backoff_never_exceeds_cap():
    for n in range(0, 30):
        assert compute_sales_action_backoff_seconds(n) <= 300


# ---------------------------------------------------------------------------
# digest idempotency key (brief Step 1 verbatim)
# ---------------------------------------------------------------------------


def test_digest_idempotency_key_morning():
    assert (
        build_digest_idempotency_key("morning_digest", "t1", "a1", "u1", date(2026, 7, 10))
        == "morning_digest:t1:a1:u1:2026-07-10"
    )


def test_digest_idempotency_key_evening():
    assert (
        build_digest_idempotency_key("evening_digest", "t1", "a1", "u1", date(2026, 7, 10))
        == "evening_digest:t1:a1:u1:2026-07-10"
    )


def test_digest_idempotency_key_distinguishes_date():
    a = build_digest_idempotency_key("morning_digest", "t1", "a1", "u1", date(2026, 7, 10))
    b = build_digest_idempotency_key("morning_digest", "t1", "a1", "u1", date(2026, 7, 11))
    assert a != b


# ---------------------------------------------------------------------------
# digest window helper
# ---------------------------------------------------------------------------


def test_within_digest_window_morning_hit():
    # 09:05 Shanghai == 01:05 UTC
    now = datetime(2026, 7, 10, 1, 5, tzinfo=timezone.utc)
    assert within_digest_window(now, "Asia/Shanghai", "09:00", grace_hours=2) is True


def test_within_digest_window_morning_before():
    # 08:00 Shanghai == 00:00 UTC -> before window
    now = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    assert within_digest_window(now, "Asia/Shanghai", "09:00", grace_hours=2) is False


def test_within_digest_window_morning_after_grace():
    # 12:01 Shanghai == 04:01 UTC -> past grace
    now = datetime(2026, 7, 10, 4, 1, tzinfo=timezone.utc)
    assert within_digest_window(now, "Asia/Shanghai", "09:00", grace_hours=2) is False


def test_within_digest_window_evening_hit():
    # 18:30 Shanghai == 10:30 UTC
    now = datetime(2026, 7, 10, 10, 30, tzinfo=timezone.utc)
    assert within_digest_window(now, "Asia/Shanghai", "18:30", grace_hours=2) is True


# ---------------------------------------------------------------------------
# scope_from_reminder
# ---------------------------------------------------------------------------


def test_scope_from_reminder_builds_scope_with_dingtalk_user_id():
    class _Reminder:
        tenant_id = "t1"
        agent_id = "a1"
        user_id = "u1"

    class _Card:
        dingtalk_user_id = "du1"

    scope = scope_from_reminder(_Reminder(), _Card())
    assert scope.tenant_id == "t1"
    assert scope.agent_id == "a1"
    assert scope.user_id == "u1"
    assert scope.dingtalk_user_id == "du1"
    assert scope.channel == "dingtalk"


def test_scope_from_reminder_without_card_has_no_dingtalk_user():
    class _Reminder:
        tenant_id = "t1"
        agent_id = "a1"
        user_id = "u1"

    scope = scope_from_reminder(_Reminder(), None)
    assert scope.dingtalk_user_id is None
