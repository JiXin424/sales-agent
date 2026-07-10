"""销售动作仓储状态机单元测试（无 DB）。

断言状态机常量、终态集合、幂等键格式与操作结果数据结构——均为纯函数/
常量，不需要数据库连接。
"""

from datetime import datetime, timezone

from sales_agent.models.sales_action import (
    SalesActionCard,
    SalesActionDelivery,
    SalesActionEvent,
    SalesActionReminder,
)
from sales_agent.services.sales_actions.repository import (
    ACTIVE_REMINDER_STATUSES,
    REMINDER_CANCEL_TARGETS,
    TERMINAL_ACTION_STATUSES,
    ActionStateResult,
    one_time_idempotency_key,
    snooze_idempotency_key,
)
from sales_agent.services.sales_actions.service import SalesActionOperationResult


def test_models_importable() -> None:
    """冒烟测试：四个模型均可正常导入并注册到 metadata。"""
    from sales_agent.core.database import Base

    for model in (SalesActionCard, SalesActionReminder, SalesActionDelivery, SalesActionEvent):
        assert model.__tablename__ in Base.metadata.tables


# ---------------------------------------------------------------------------
# State machine: terminal statuses & reminder targets
# ---------------------------------------------------------------------------

def test_terminal_action_statuses_are_done_and_cancelled() -> None:
    """A sales action can only end in 'done' or 'cancelled'."""
    assert TERMINAL_ACTION_STATUSES == frozenset({"done", "cancelled"})


def test_active_reminder_statuses_are_scheduled_and_sending() -> None:
    """Reminders that are still in-flight (cancellable / claimable)."""
    assert ACTIVE_REMINDER_STATUSES == frozenset({"scheduled", "sending"})


def test_reminder_cancel_targets_cover_in_flight() -> None:
    """complete/cancel must neutralize both scheduled and sending reminders."""
    assert REMINDER_CANCEL_TARGETS >= ACTIVE_REMINDER_STATUSES


def test_terminal_status_excludes_pending() -> None:
    assert "pending" not in TERMINAL_ACTION_STATUSES
    assert "pending" not in ACTIVE_REMINDER_STATUSES


# ---------------------------------------------------------------------------
# Idempotency key formats (verbatim from the spec)
# ---------------------------------------------------------------------------

def test_one_time_idempotency_key_format() -> None:
    """one_time:{tenant}:{agent}:{user}:{action_id}:{scheduled_at}"""
    when = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
    key = one_time_idempotency_key("t1", "a1", "u1", "act123", when)
    assert key == "one_time:t1:a1:u1:act123:2026-07-10T15:30:00+00:00"


def test_snooze_idempotency_key_format() -> None:
    """snooze:{action_id}:{event_id}:{new_time}"""
    new_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)
    key = snooze_idempotency_key("act123", "evt9", new_time)
    assert key == "snooze:act123:evt9:2026-07-10T16:00:00+00:00"


def test_idempotency_keys_differ_per_action() -> None:
    when = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
    k1 = one_time_idempotency_key("t1", "a1", "u1", "act1", when)
    k2 = one_time_idempotency_key("t1", "a1", "u1", "act2", when)
    assert k1 != k2


# ---------------------------------------------------------------------------
# Result dataclass shapes (cross-task contract)
# ---------------------------------------------------------------------------

def test_action_state_result_carries_status_and_reason() -> None:
    res = ActionStateResult(action_id="a1", status="done", reason_code="completed")
    assert res.status == "done"
    assert res.reason_code == "completed"
    assert res.action_id == "a1"


def test_operation_result_default_response_kind_is_sales_action() -> None:
    res = SalesActionOperationResult(
        operation="create",
        status="created",
        response_text="已创建提醒",
        reason_code="created",
    )
    # default response_kind is sales_action so Task 4 can rely on it
    assert res.response_kind == "sales_action"
    assert res.action_id is None
    assert res.scheduled_at is None
    assert res.reason_code == "created"


def test_operation_result_can_signal_chat_fallthrough() -> None:
    res = SalesActionOperationResult(
        response_kind="chat",
        operation="ignore",
        status="not_handled",
        response_text="",
        reason_code="not_an_action",
    )
    assert res.response_kind == "chat"
