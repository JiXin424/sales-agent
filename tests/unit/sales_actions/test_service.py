"""销售动作编排服务单元测试（无 DB / 无真实 LLM）。

用假 chat_model（返回 canned JSON）+ 存根 repo 验证 :meth:`handle_message`
的意图路由、回执文案与 :class:`SalesActionOperationResult` 形状——这是 Task 4
（Online Graph）依赖的跨任务契约。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sales_agent.services.sales_actions import (
    SalesActionOperationResult,
    SalesActionScope,
    SalesActionService,
)


def _scope() -> SalesActionScope:
    return SalesActionScope(tenant_id="t1", agent_id="a1", user_id="u1", dingtalk_user_id="du1")


class FakeChatModel:
    """Returns a canned SalesActionExtraction JSON keyed by a marker in the message."""

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls = 0

    async def generate(self, messages, temperature=None, max_tokens=None, response_format=None):
        self.calls += 1
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        for marker, payload in self.responses.items():
            if marker in user:
                return json.dumps(payload, ensure_ascii=False)
        # default: not an action
        return json.dumps({"intent": "none", "confidence": 0.0}, ensure_ascii=False)


class StubRepo:
    """Minimal async stub matching the repo methods handle_message touches."""

    def __init__(self):
        self.created = []
        self.pending = []
        self.completed = []
        self.cancelled = []
        self.snoozed = []
        self.snooze_result = None

    async def create_action(self, scope, **kw):
        card = SimpleNamespace(id="card-1", title=kw["title"], status="pending",
                               customer_name=kw.get("customer_name"),
                               scheduled_at=kw["scheduled_at"])
        self.created.append(kw)
        return card

    async def list_actions(self, scope, *, status=None):
        return list(self.pending)

    async def complete_action(self, scope, action_id, *, event_id=None):
        from sales_agent.services.sales_actions.repository import ActionStateResult
        self.completed.append(action_id)
        return ActionStateResult(action_id=action_id, status="done", reason_code="completed")

    async def cancel_action(self, scope, action_id, *, event_id=None):
        from sales_agent.services.sales_actions.repository import ActionStateResult
        self.cancelled.append(action_id)
        return ActionStateResult(action_id=action_id, status="cancelled", reason_code="cancelled")

    async def snooze_action(self, scope, action_id, *, event_id, new_time):
        self.snoozed.append((action_id, new_time))
        if self.snooze_result is not None:
            return self.snooze_result
        return SimpleNamespace(reason_code="snoozed")


NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_create_path_returns_created_result():
    fake = FakeChatModel({
        "MARKER_CREATE": {
            "intent": "create_action", "explicit_create": True, "confidence": 0.95,
            "title": "给张总回电话", "customer_name": "张总", "action_type": "call_back",
            "scheduled_at": "2026-07-10T15:30:00+00:00", "timezone": "Asia/Shanghai",
        }
    })
    svc = SalesActionService(StubRepo(), fake)
    res = await svc.handle_message(
        scope=_scope(), message="MARKER_CREATE", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert isinstance(res, SalesActionOperationResult)
    assert res.response_kind == "sales_action"
    assert res.operation == "create"
    assert res.status == "created"
    assert res.action_id == "card-1"
    assert res.reason_code == "created"
    assert "已创建提醒" in res.response_text
    assert res.scheduled_at is not None


@pytest.mark.asyncio
async def test_clarify_missing_time_path():
    fake = FakeChatModel({
        "MARKER_NO_TIME": {
            "intent": "create_action", "explicit_create": True, "confidence": 0.9,
            "title": "给张总回电话", "action_type": "call_back",
            "timezone": "Asia/Shanghai",
        }
    })
    svc = SalesActionService(StubRepo(), fake)
    res = await svc.handle_message(
        scope=_scope(), message="MARKER_NO_TIME", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.operation == "clarify"
    assert res.status == "clarify"
    assert "几点" in res.response_text or "什么时候" in res.response_text


@pytest.mark.asyncio
async def test_suggest_path_for_non_explicit():
    fake = FakeChatModel({
        "MARKER_SUGGEST": {
            "intent": "suggest_action", "explicit_create": False, "confidence": 0.9,
            "title": "准备报价单", "action_type": "send_proposal",
            "scheduled_at": "2026-07-11T10:00:00+00:00", "timezone": "Asia/Shanghai",
        }
    })
    svc = SalesActionService(StubRepo(), fake)
    res = await svc.handle_message(
        scope=_scope(), message="MARKER_SUGGEST", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.operation == "suggest"
    assert res.status == "suggested"
    assert "建议下一步" in res.response_text


@pytest.mark.asyncio
async def test_not_an_action_falls_through_to_chat():
    fake = FakeChatModel({})  # default → none
    svc = SalesActionService(StubRepo(), fake)
    res = await svc.handle_message(
        scope=_scope(), message="今天天气不错", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.response_kind == "chat"
    assert res.operation == "ignore"
    assert res.response_text == ""


@pytest.mark.asyncio
async def test_complete_path_resolves_single_pending_task():
    repo = StubRepo()
    repo.pending = [SimpleNamespace(id="card-x", title="回电张总", customer_name="张总",
                                    status="pending", scheduled_at=NOW)]
    # fast detector catches "搞定了" → complete branch; LLM gives customer name
    fake = FakeChatModel({
        "搞定了": {
            "intent": "complete_action", "confidence": 0.9,
            "customer_name": "张总",
        }
    })
    svc = SalesActionService(repo, fake)
    res = await svc.handle_message(
        scope=_scope(), message="张总那个搞定了", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.operation == "complete"
    assert res.status == "done"
    assert res.action_id == "card-x"
    assert repo.completed == ["card-x"]


@pytest.mark.asyncio
async def test_list_path_returns_pending_summary():
    repo = StubRepo()
    repo.pending = [
        SimpleNamespace(id="c1", title="任务A", customer_name="张总",
                        status="pending", scheduled_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)),
    ]
    svc = SalesActionService(repo, FakeChatModel({}))
    res = await svc.handle_message(
        scope=_scope(), message="我还有哪些任务", conversation_id="conv",
        topic_id=None, source_event_id=None, now=NOW,
    )
    assert res.operation == "list"
    assert res.status == "listed"
    assert "任务A" in res.response_text
    assert res.actions and res.actions[0]["action_id"] == "c1"


@pytest.mark.asyncio
async def test_snooze_on_terminal_action_does_not_fake_success():
    """If snooze_action returns already_terminal, surface it — don't claim snoozed."""
    from sales_agent.services.sales_actions.repository import ActionStateResult

    repo = StubRepo()
    repo.pending = [SimpleNamespace(id="card-x", title="回电张总", customer_name="张总",
                                    status="pending", scheduled_at=NOW)]
    repo.snooze_result = ActionStateResult(action_id="card-x", status="done", reason_code="already_terminal")
    fake = FakeChatModel({
        "推迟": {
            "intent": "snooze_action", "confidence": 0.9,
            "customer_name": "张总", "scheduled_at": "2026-07-10T16:00:00+00:00",
            "timezone": "Asia/Shanghai",
        }
    })
    svc = SalesActionService(repo, fake)
    res = await svc.handle_message(
        scope=_scope(), message="把张总那个推迟到下午4点", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.operation == "snooze"
    assert res.status == "done"  # the real state, not faked as "snoozed"
    assert res.reason_code == "already_terminal"
    assert "已推迟" not in res.response_text


@pytest.mark.asyncio
async def test_snooze_path_creates_reminder_when_healthy():
    repo = StubRepo()
    repo.pending = [SimpleNamespace(id="card-x", title="回电张总", customer_name="张总",
                                    status="pending", scheduled_at=NOW)]
    fake = FakeChatModel({
        "推迟": {
            "intent": "snooze_action", "confidence": 0.9,
            "customer_name": "张总", "scheduled_at": "2026-07-10T16:00:00+00:00",
            "timezone": "Asia/Shanghai",
        }
    })
    svc = SalesActionService(repo, fake)
    res = await svc.handle_message(
        scope=_scope(), message="把张总那个推迟到下午4点", conversation_id="c1",
        topic_id=None, source_event_id="e1", now=NOW,
    )
    assert res.operation == "snooze"
    assert res.status == "snoozed"
    assert "已推迟" in res.response_text
    assert repo.snoozed  # called


@pytest.mark.asyncio
async def test_state_intent_discards_stale_clarification_partial():
    """A list/complete message drops a prior create-clarification partial so a
    later create doesn't merge with the stale title."""
    repo = StubRepo()
    fake = FakeChatModel({
        # first create: missing time → clarify, stashes partial title "打电话"
        "MARKER_NOTIME": {
            "intent": "create_action", "explicit_create": True, "confidence": 0.9,
            "title": "打电话", "action_type": "call_back", "timezone": "Asia/Shanghai",
        },
        # later create: fully specified, different title
        "MARKER_FULL": {
            "intent": "create_action", "explicit_create": True, "confidence": 0.9,
            "title": "拜访客户", "action_type": "visit_prepare",
            "scheduled_at": "2026-07-11T10:00:00+00:00", "timezone": "Asia/Shanghai",
        },
    })
    svc = SalesActionService(repo, fake)
    scope = _scope()
    # 1) create with missing time → clarify, stashes "打电话"
    await svc.handle_message(scope=scope, message="MARKER_NOTIME", conversation_id="c1",
                             topic_id=None, source_event_id="e1", now=NOW)
    # 2) list message in between → must discard the stale partial
    await svc.handle_message(scope=scope, message="我还有哪些任务", conversation_id="c1",
                             topic_id=None, source_event_id=None, now=NOW)
    # 3) a new full create → must use the NEW title, not the stale "打电话"
    res = await svc.handle_message(scope=scope, message="MARKER_FULL", conversation_id="c1",
                                   topic_id=None, source_event_id="e2", now=NOW)
    assert res.operation == "create"
    assert repo.created[-1]["title"] == "拜访客户"

