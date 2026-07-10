"""DingTalk + Online Graph integration for sales actions (Task 4).

Drives the *real* Online Graph (via :func:`invoke_online_turn`) with a real
DB session and a fake chat model so that an explicit reminder message is
routed to ``sales_action_command_node``, the :class:`SalesActionService`
creates a real :class:`SalesActionCard` row, and the resulting state fields
flow back through the graph.

A second test exercises the DingTalk processor end-to-end
(:func:`handle_dingtalk_event`) to prove the sales-action fields reach the
:class:`DingTalkTurnResult`.

No real LLM is contacted — the fake chat model returns canned extraction JSON.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from sales_agent.core.tenant_runtime import TenantRuntime
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event
from sales_agent.integrations.dingtalk.agent_resolver import resolve_dingtalk_agent_id
from sales_agent.models.sales_action import SalesActionCard
from sales_agent.services.online_conversation import invoke_online_turn

# Ensure the DingTalk ORM models are registered in Base.metadata before the
# db_session fixture runs ``create_all`` (they are imported lazily inside the
# processor, which is too late for schema creation).
import sales_agent.integrations.dingtalk.models  # noqa: F401


# ====================================================================
# Fake chat model — returns canned SalesActionExtraction JSON
# ====================================================================


class FakeCreateChatModel:
    """Always returns a valid ``create_action`` extraction (future time)."""

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {
            "intent": "create_action",
            "explicit_create": True,
            "confidence": 0.95,
            "title": "给张总回电话",
            "customer_name": "张总",
            "action_type": "call_back",
            "scheduled_at": "2026-07-11T10:00:00+00:00",
            "timezone": "Asia/Shanghai",
        }
        self.calls = 0

    async def generate(self, messages, temperature=None, max_tokens=None, response_format=None):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


# ====================================================================
# invoke_online_turn — real graph + real DB create path
# ====================================================================


@pytest.fixture(autouse=True)
def _enable_sales_actions(monkeypatch):
    """Sales actions ship disabled by default; enable for this suite so the
    Online Graph routes explicit action commands to the sales-action node
    (mirrors the runtime setting `sales_actions.enabled=true`)."""
    from sales_agent.core.config import get_settings
    monkeypatch.setattr(get_settings().sales_actions, "enabled", True)


@pytest.mark.asyncio
async def test_invoke_online_turn_creates_sales_action(
    db_session, sample_tenant, active_agent,
):
    """An explicit reminder message routed through the Online Graph creates a
    pending SalesActionCard and surfaces the create metadata in the result."""
    result = await invoke_online_turn(
        db=db_session,
        tenant_id=sample_tenant,
        agent_id=active_agent.id,
        user_id="test_user_001",
        session_user_id="dt_staff_1",
        channel="dingtalk",
        conversation_id="conv-sales-1",
        message="提醒我半小时后给张总回电话",
        event_id="evt-create-1",
        chat_model=FakeCreateChatModel(),
        embedding_model=MagicMock(),  # short-circuit model resolution (no tenant match)
        checkpointer=InMemorySaver(),
    )

    # Graph routed to sales_action and created the action.
    assert result["response_kind"] == "sales_action"
    assert result["sales_action_operation"] == "create"
    assert result["sales_action_status"] == "created"
    action_id = result["sales_action_id"]
    assert action_id
    assert result["sales_action_reason_code"] == "created"
    assert "张总" in result["answer_dict"]["summary"]

    # A real pending card + reminder row were persisted in the same transaction.
    card = (
        await db_session.execute(
            select(SalesActionCard).where(SalesActionCard.id == action_id)
        )
    ).scalar_one()
    assert card.title == "给张总回电话"
    assert card.customer_name == "张总"
    assert card.status == "pending"
    assert card.tenant_id == sample_tenant
    assert card.dingtalk_user_id == "dt_staff_1"


@pytest.mark.asyncio
async def test_invoke_online_turn_clarify_sets_pending_flag(
    db_session, sample_tenant, active_agent,
):
    """A create missing the time clarifies and sets the pending-clarification
    flag so the next turn routes back to the sales-action node."""
    fake = FakeCreateChatModel(payload={
        "intent": "create_action",
        "explicit_create": True,
        "confidence": 0.95,
        "title": "给张总回电话",
        "customer_name": "张总",
        "action_type": "call_back",
        "timezone": "Asia/Shanghai",
        # no scheduled_at → missing_time clarify
    })
    result = await invoke_online_turn(
        db=db_session,
        tenant_id=sample_tenant,
        agent_id=active_agent.id,
        user_id="test_user_001",
        session_user_id="dt_staff_1",
        channel="dingtalk",
        conversation_id="conv-sales-2",
        message="提醒我给张总回电话",
        event_id="evt-clarify-1",
        chat_model=fake,
        embedding_model=MagicMock(),  # short-circuit model resolution (no tenant match)
        checkpointer=InMemorySaver(),
    )
    assert result["response_kind"] == "sales_action"
    assert result["sales_action_operation"] == "clarify"
    assert result["sales_action_pending_clarification"] == "missing_time"
    assert "几点" in result["answer_dict"]["summary"]


# ====================================================================
# Two-turn clarification completion (regression: review Important I-1)
# ====================================================================


class FakeMarkerChatModel:
    """Return canned extraction JSON keyed by a substring marker in the message.

    Mirrors the FakeChatModel pattern in tests/unit/sales_actions/test_service.py.
    Lets turn-1 and turn-2 of a clarification flow return *different* extractions
    (turn-1 has the title but no time; turn-2 has the time but no title) so the
    merge can be exercised without a real LLM.
    """

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls = 0

    async def generate(self, messages, temperature=None, max_tokens=None, response_format=None):
        self.calls += 1
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        for marker, payload in self.responses.items():
            if marker in user:
                return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"intent": "none", "confidence": 0.0}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_two_turn_clarification_completes_via_checkpoint(
    db_session, sample_tenant, active_agent,
):
    """Cross-turn clarification COMPLETION works end-to-end.

    Turn 1: "提醒我给张总回电话" (no time) → clarify, state carries the partial
    extraction (title/customer) and the pending flag.

    Turn 2: "下午3点" → the partial from checkpoint state merges with the
    turn-2 time → a SalesActionCard IS created with the merged title + the
    scheduled time; the partial and pending flag are cleared.

    This is the regression that was missing: the node constructs a fresh
    SalesActionService each turn, so the (now checkpoint-passed) partial is
    the only way the merge can find the turn-1 title.
    """
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    fake = FakeMarkerChatModel({
        # Turn 1: title + customer, but NO scheduled_at → missing_time clarify.
        "给张总回电话": {
            "intent": "create_action",
            "explicit_create": True,
            "confidence": 0.95,
            "title": "给张总回电话",
            "customer_name": "张总",
            "action_type": "call_back",
            "timezone": "Asia/Shanghai",
        },
        # Turn 2: a time only (no title) → after merge title comes from turn 1.
        "3点": {
            "intent": "create_action",
            "explicit_create": True,
            "confidence": 0.9,
            "scheduled_at": "2026-07-11T15:00:00+00:00",
            "timezone": "Asia/Shanghai",
        },
    })
    # One shared in-memory checkpointer so turn-2 reads turn-1's checkpoint.
    checkpointer = InMemorySaver()

    # ── Turn 1: missing time → clarify + stash partial ───────────────
    turn1 = await invoke_online_turn(
        db=db_session,
        tenant_id=sample_tenant,
        agent_id=active_agent.id,
        user_id="test_user_002",
        session_user_id="dt_staff_2",
        channel="dingtalk",
        conversation_id="conv-clarify-2turn",
        message="提醒我给张总回电话",
        event_id="evt-2turn-1",
        chat_model=fake,
        embedding_model=MagicMock(),
        now=now,
        checkpointer=checkpointer,
    )
    assert turn1["sales_action_operation"] == "clarify"
    assert turn1["sales_action_pending_clarification"] == "missing_time"
    # The partial extraction (with the title) is carried in checkpoint state.
    partial = turn1["sales_action_pending_partial"]
    assert partial is not None
    assert "给张总回电话" in partial["title"]
    assert partial["customer_name"] == "张总"

    # ── Turn 2: "下午3点" → merge completes → create ────────────────
    turn2 = await invoke_online_turn(
        db=db_session,
        tenant_id=sample_tenant,
        agent_id=active_agent.id,
        user_id="test_user_002",
        session_user_id="dt_staff_2",  # same session → same thread_id → same checkpoint
        channel="dingtalk",
        conversation_id="conv-clarify-2turn",
        message="下午3点",
        event_id="evt-2turn-2",
        chat_model=fake,
        embedding_model=MagicMock(),
        now=now,
        checkpointer=checkpointer,
    )
    assert turn2["sales_action_operation"] == "create"
    assert turn2["sales_action_status"] == "created"
    action_id = turn2["sales_action_id"]
    assert action_id
    # The partial + pending flag are cleared on completion.
    assert turn2["sales_action_pending_clarification"] is None
    assert turn2["sales_action_pending_partial"] is None

    # A real card was persisted with the MERGED title + the turn-2 time.
    card = (
        await db_session.execute(
            select(SalesActionCard).where(SalesActionCard.id == action_id)
        )
    ).scalar_one()
    assert "张总回电话" in card.title
    assert card.customer_name == "张总"
    assert card.status == "pending"
    assert card.scheduled_at == datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)


# ====================================================================
# handle_dingtalk_event — full DingTalk processor mapping
# ====================================================================


@pytest.mark.asyncio
async def test_handle_dingtalk_event_surfaces_sales_action(
    db_session, sample_tenant, active_agent, monkeypatch,
):
    """The DingTalk processor populates DingTalkTurnResult.sales_action_* from
    the graph result and replies with the create acknowledgement.

    ``handle_dingtalk_event`` invokes the Online Graph internally via
    ``invoke_online_turn`` (which would resolve a real model provider); we
    monkeypatch that call to return a canned sales-action result so no real
    network/LLM is contacted. This isolates the processor's field mapping —
    the actual Task-4 change under test here.
    """
    from sales_agent.core.config import get_settings
    import sales_agent.integrations.dingtalk.processor as processor_mod

    settings = get_settings()
    config = DingTalkConfig()
    runtime = TenantRuntime(
        tenant_id=sample_tenant,
        tenant_name="Test Tenant",
        deployment_mode="dedicated",
    )

    canned_result = {
        "answer_dict": {
            "summary": "已创建提醒：2026-07-11 10:00，提醒你给张总回电话。",
            "sections": [],
        },
        "response_kind": "sales_action",
        "sales_action_operation": "create",
        "sales_action_status": "created",
        "sales_action_id": "card-dt-1",
        "sales_action_scheduled_at": datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc),
        "sales_action_reason_code": "created",
        "last_event_id": "evt-dt-1",
    }
    monkeypatch.setattr(
        processor_mod, "invoke_online_turn",
        AsyncMock(return_value=canned_result),
    )

    replies: list[str] = []

    async def reply_fn(text: str) -> None:
        replies.append(text)

    result = await handle_dingtalk_event(
        db=db_session,
        config=config,
        settings=settings,
        runtime=runtime,
        event_id="evt-dt-1",
        corp_id="corp1",
        sender_id="dt_staff_1",
        sender_name="Salesperson",
        message_type="text",
        text="提醒我半小时后给张总回电话",
        dingtalk_conversation_id="dt-conv-1",
        reply_fn=reply_fn,
    )

    assert result.response_kind == "sales_action"
    assert result.sales_action_operation == "create"
    assert result.sales_action_status == "created"
    assert result.sales_action_id == "card-dt-1"
    assert result.sales_action_reason_code == "created"
    assert result.sales_action_scheduled_at == datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    # A reply was sent to the user containing the acknowledgement.
    assert replies, "expected a DingTalk reply for the created action"
    assert "张总" in replies[0]
