"""Tests for bounded topic restore — unique restore, multi-candidate anchor,
suffix execution, scoped history, and the control response path.

These tests use a REAL database (``db_session``) for ConversationTopic
persistence and drive ``context_resolution_node`` directly with a real
``TopicManager`` plus deterministic chat-model / resolver stubs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_agent.graph.online.nodes import context_resolution_node
from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services.structured_router_output import ContextDecision
from sales_agent.services.topic_manager import TopicManager


# ===================================================================
# Constants / helpers
# ===================================================================

SCOPE = {"tenant_id": "t1", "agent_id": "a1", "user_id": "u1", "channel": "dingtalk"}
CONVERSATION_ID = "c1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_config(ctx: dict) -> dict:
    return {"configurable": {"__pregel_runtime": SimpleNamespace(context=ctx)}}


class StubModel:
    """Stub chat model returning a canned raw response string."""

    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[list[dict]] = []

    async def generate(self, messages, temperature=0.0, max_tokens=500):
        self.calls.append(messages)
        return self.response


def _passthrough_context_resolver(capture: dict | None = None):
    """A context resolver that returns standalone_query == message (continue).

    Optionally captures the ``recent_messages`` it received.
    """

    async def _fn(*, message, topic, recent_messages, chat_model, db, tenant_id, agent_id):
        if capture is not None:
            capture["recent_messages"] = list(recent_messages)
            capture["topic"] = topic
        return ContextDecision(
            turn_relation="continue",
            standalone_query=message,
            retained_entities=[],
            retracted_goals=[],
            missing_references=[],
            confidence=0.9,
            reason_code="restore_suffix",
        )

    return _fn


# ===================================================================
# Fixtures
# ===================================================================


async def _make_closed_topic(
    db_session,
    *,
    topic_id: str | None = None,
    summary: str = "福多多产品介绍",
    goal: str = "介绍福多多",
    age: timedelta = timedelta(minutes=31),
    conversation_id: str = CONVERSATION_ID,
) -> ConversationTopic:
    """Insert a closed topic closed ``age`` ago (inside the 24h window)."""
    now = _now()
    topic = ConversationTopic(
        id=topic_id or f"t-{uuid.uuid4().hex[:10]}",
        tenant_id=SCOPE["tenant_id"],
        agent_id=SCOPE["agent_id"],
        user_id=SCOPE["user_id"],
        channel=SCOPE["channel"],
        conversation_id=conversation_id,
        status="closed",
        summary=summary,
        current_goal=goal,
        closed_at=now - age,
        last_active_at=now - age - timedelta(minutes=1),
        expires_at=now - age,
    )
    db_session.add(topic)
    await db_session.flush()
    return topic


async def _make_message(
    db_session,
    *,
    topic_id: str,
    role: str,
    content: str,
    conversation_id: str = CONVERSATION_ID,
) -> None:
    """Insert a conversation_messages row scoped to a topic."""
    from sales_agent.models.conversation import ConversationMessage

    db_session.add(
        ConversationMessage(
            tenant_id=SCOPE["tenant_id"],
            agent_id=SCOPE["agent_id"],
            user_id=SCOPE["user_id"],
            conversation_id=conversation_id,
            topic_id=topic_id,
            role=role,
            content=content,
        )
    )
    await db_session.flush()


@pytest.fixture
def manager() -> TopicManager:
    return TopicManager()


async def run_context_turn(
    db_session,
    *,
    message: str,
    chat_model=None,
    context_resolver=None,
    event_id: str | None = None,
    capture: dict | None = None,
    conversation_id: str = CONVERSATION_ID,
) -> dict:
    """Drive ``context_resolution_node`` with a real TopicManager + db.

    ``chat_model`` defaults to a stub returning ``new`` for the restore
    resolver (safe no-restore fallback). ``context_resolver`` defaults to a
    passthrough that echoes the message as ``standalone_query``.
    """
    restore_model = chat_model or StubModel(
        json.dumps(
            {
                "resolution": "new",
                "selected_topic_id": None,
                "supplemental_message": None,
                "confidence": 0.9,
                "reason_code": "default_new",
            }
        )
    )
    ctx = {
        "db": db_session,
        "chat_model": restore_model,
        "now": _now(),
        "topic_manager": TopicManager(),
        "context_resolver_override": context_resolver or _passthrough_context_resolver(capture),
    }
    cfg = _build_config(ctx)
    state = {
        **SCOPE,
        "conversation_id": conversation_id,
        "message": message,
        "event_id": event_id or f"ev-{uuid.uuid4().hex[:8]}",
    }
    return await context_resolution_node(state, cfg)


# ===================================================================
# Step 1: Unique restore tests
# ===================================================================


@pytest.mark.asyncio
async def test_explicit_continue_restores_unique_topic(db_session, manager):
    """Exact '继续' with one restorable candidate restores it (control response)."""
    old_topic = await _make_closed_topic(db_session, summary="福多多产品介绍")

    result = await run_context_turn(db_session, message="继续")

    await db_session.flush()
    await db_session.refresh(old_topic)
    assert old_topic.status == "active"
    assert result["topic_id"] == old_topic.id
    assert result["turn_relation"] == "continue"
    assert result["context_status"] == "control"
    assert result["response_kind"] == "topic_restored"
    assert result["standalone_query"] == ""


@pytest.mark.asyncio
async def test_continue_with_suffix_restores_and_executes_suffix(db_session, manager):
    """'继续，帮我查一下价格' restores the topic and runs the suffix."""
    old_topic = await _make_closed_topic(db_session, summary="福多多产品介绍")

    result = await run_context_turn(db_session, message="继续，帮我查一下价格")

    await db_session.flush()
    await db_session.refresh(old_topic)
    assert old_topic.status == "active"
    assert result["context_status"] == "resolved"
    assert "帮我查一下价格" in result["standalone_query"]
    assert result["topic_id"] == old_topic.id


@pytest.mark.asyncio
async def test_ordinary_unrelated_input_after_expiry_creates_new_topic(db_session, manager):
    """An unrelated message after expiry creates a new topic and does not restore."""
    old_topic = await _make_closed_topic(db_session, summary="福多多产品介绍")

    result = await run_context_turn(db_session, message="查东方福利网的产品")

    await db_session.flush()
    await db_session.refresh(old_topic)
    assert old_topic.status == "closed"  # not restored
    assert result["context_status"] == "resolved"
    assert result["topic_id"] is not None
    assert result["topic_id"] != old_topic.id


@pytest.mark.asyncio
async def test_active_topic_with_resolver_new_closes_old_and_creates_new(db_session, manager):
    """Resolver 'new' on an active topic closes it and creates a fresh active topic."""
    now = _now()
    active = ConversationTopic(
        id="t-active-new",
        tenant_id=SCOPE["tenant_id"],
        agent_id=SCOPE["agent_id"],
        user_id=SCOPE["user_id"],
        channel=SCOPE["channel"],
        conversation_id=CONVERSATION_ID,
        status="active",
        summary="旧话题",
        current_goal="旧目标",
        last_active_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
    )
    db_session.add(active)
    await db_session.flush()

    async def _new_resolver(*, message, topic, recent_messages, chat_model, db, tenant_id, agent_id):
        return ContextDecision(
            turn_relation="new",
            standalone_query=message,
            retained_entities=[],
            retracted_goals=[],
            missing_references=[],
            confidence=0.9,
            reason_code="new_topic",
        )

    result = await run_context_turn(
        db_session, message="查竞品分析", context_resolver=_new_resolver,
    )

    await db_session.flush()
    await db_session.refresh(active)
    assert active.status == "closed"
    assert active.closed_at is not None
    assert result["topic_id"] is not None
    assert result["topic_id"] != active.id  # new active id, not the closed one
    # The new topic is active
    new_active = await manager.get_active_topic(
        db_session, SCOPE["tenant_id"], SCOPE["agent_id"], SCOPE["user_id"], SCOPE["channel"],
    )
    assert new_active is not None
    assert new_active.id == result["topic_id"]


@pytest.mark.asyncio
async def test_scoped_history_only_loads_active_topic_messages(db_session, manager):
    """load_recent_topic_messages returns only the restored topic's messages,
    in chronological order, limited to 6."""
    old_topic = await _make_closed_topic(db_session, topic_id="t-restore-scope", summary="话题A")
    other = await _make_closed_topic(
        db_session, topic_id="t-other-scope", summary="话题B", age=timedelta(minutes=40),
    )
    # 3 messages on old_topic (should be loaded after restore) + 2 on other (must NOT leak)
    for i in range(3):
        await _make_message(
            db_session, topic_id=old_topic.id, role="user", content=f"A-user-{i}",
        )
        await _make_message(
            db_session, topic_id=old_topic.id, role="assistant", content=f"A-assistant-{i}",
        )
    await _make_message(db_session, topic_id=other.id, role="user", content="B-leak-1")
    await _make_message(db_session, topic_id=other.id, role="assistant", content="B-leak-2")

    capture: dict = {}
    # Restore with a suffix so the resolver runs on the restored topic.
    await run_context_turn(
        db_session,
        message="继续，再补充一点",
        context_resolver=_passthrough_context_resolver(capture),
    )

    recent = capture.get("recent_messages", [])
    contents = [m["content"] for m in recent]
    assert all("B-leak" not in c for c in contents), "closed non-selected topic leaked"
    # limited to 6, chronological (oldest first)
    assert len(recent) <= 6
    if len(recent) >= 2:
        assert recent[0]["content"] != recent[-1]["content"]


# ===================================================================
# Step 2: Multiple-candidate tests
# ===================================================================


async def _seed_two_candidates(db_session) -> list[ConversationTopic]:
    """Two restorable candidates with distinct summaries."""
    a = await _make_closed_topic(
        db_session, topic_id="t-cand-a", summary="福多多价格",
        goal="查福多多价格", age=timedelta(minutes=35),
    )
    b = await _make_closed_topic(
        db_session, topic_id="t-cand-b", summary="竞品分析",
        goal="做竞品分析", age=timedelta(minutes=45),
    )
    return [a, b]


@pytest.mark.asyncio
async def test_continue_with_multiple_candidates_creates_anchor(db_session, manager):
    """Exact '继续' with 2+ candidates creates one active anchor + numbered list."""
    cand_a, cand_b = await _seed_two_candidates(db_session)

    result = await run_context_turn(db_session, message="继续")

    await db_session.flush()
    assert result["context_status"] == "clarify"
    assert result["response_kind"] == "clarify"

    # Exactly one active topic (the anchor), neither candidate is active.
    active = await manager.get_active_topic(
        db_session, SCOPE["tenant_id"], SCOPE["agent_id"], SCOPE["user_id"], SCOPE["channel"],
    )
    assert active is not None
    assert active.id not in (cand_a.id, cand_b.id)
    await db_session.refresh(cand_a)
    await db_session.refresh(cand_b)
    assert cand_a.status == "closed"
    assert cand_b.status == "closed"

    # Anchor stores topic_restore pending JSON.
    pending = json.loads(active.pending_clarification_json)
    assert pending["kind"] == "topic_restore"
    assert set(pending["candidate_topic_ids"]) == {cand_a.id, cand_b.id}
    assert len(pending["candidate_summaries"]) <= 3

    # Answer lists numbered summaries.
    answer = result.get("answer_dict", {})
    summary_text = answer.get("summary", "")
    assert "1" in summary_text or "1." in summary_text or "第一个" in summary_text


@pytest.mark.asyncio
async def test_numeric_first_restores_candidate_one(db_session, manager):
    """'第1个' restores the first candidate (control response, no suffix)."""
    cand_a, cand_b = await _seed_two_candidates(db_session)

    result = await run_context_turn(db_session, message="第1个")

    await db_session.flush()
    await db_session.refresh(cand_a)
    await db_session.refresh(cand_b)
    assert cand_a.status == "active"
    assert cand_b.status == "closed"
    assert result["topic_id"] == cand_a.id
    assert result["context_status"] == "control"
    assert result["response_kind"] == "topic_restored"


@pytest.mark.asyncio
async def test_numeric_second_with_suffix_restores_and_runs_suffix(db_session, manager):
    """'第二个，继续查价格' restores candidate two and executes the suffix."""
    cand_a, cand_b = await _seed_two_candidates(db_session)

    result = await run_context_turn(db_session, message="第二个，继续查价格")

    await db_session.flush()
    await db_session.refresh(cand_a)
    await db_session.refresh(cand_b)
    assert cand_a.status == "closed"
    assert cand_b.status == "active"
    assert result["topic_id"] == cand_b.id
    assert result["context_status"] == "resolved"
    assert "继续查价格" in result["standalone_query"]


@pytest.mark.asyncio
async def test_new_command_with_suffix_closes_anchor_and_creates_clean_topic(db_session, manager):
    """Two-turn: '继续' creates anchor, then '新问题，帮我写开场白' closes anchor
    and creates a clean topic for the suffix."""
    cand_a, cand_b = await _seed_two_candidates(db_session)

    # Turn 1: create the anchor.
    await run_context_turn(db_session, message="继续", event_id="ev-anchor")
    await db_session.flush()
    anchor = await manager.get_active_topic(
        db_session, SCOPE["tenant_id"], SCOPE["agent_id"], SCOPE["user_id"], SCOPE["channel"],
    )
    assert anchor is not None
    anchor_id = anchor.id

    # Turn 2: new command with a suffix.
    result = await run_context_turn(
        db_session, message="新问题，帮我写开场白", event_id="ev-new",
    )
    await db_session.flush()
    await db_session.refresh(anchor)
    assert anchor.status == "closed"  # anchor flushed/closed
    assert result["context_status"] == "resolved"
    assert result["topic_id"] is not None
    assert result["topic_id"] != anchor_id
    assert result["topic_id"] not in (cand_a.id, cand_b.id)
    assert "帮我写开场白" in result["standalone_query"]


@pytest.mark.asyncio
async def test_model_output_with_unknown_id_is_ambiguous(db_session, manager):
    """A model-returned topic_id not in the candidate list is rejected (ambiguous)."""
    cand_a, cand_b = await _seed_two_candidates(db_session)
    bad_model = StubModel(
        json.dumps(
            {
                "resolution": "restore",
                "selected_topic_id": "not-a-real-candidate-id",
                "supplemental_message": None,
                "confidence": 0.8,
                "reason_code": "invented",
            }
        )
    )

    result = await run_context_turn(db_session, message="那个话题", chat_model=bad_model)

    await db_session.flush()
    # No restore happened.
    await db_session.refresh(cand_a)
    await db_session.refresh(cand_b)
    assert cand_a.status == "closed"
    assert cand_b.status == "closed"
    # Treated as ambiguous -> anchor created.
    assert result["context_status"] == "clarify"


@pytest.mark.asyncio
async def test_two_unresolved_answers_fallback_to_new(db_session, manager):
    """After two unresolved (ambiguous) answers, the safe fallback is 'new'."""
    cand_a, cand_b = await _seed_two_candidates(db_session)
    # A model that always answers ambiguous.
    ambig_model = StubModel(
        json.dumps(
            {
                "resolution": "ambiguous",
                "selected_topic_id": None,
                "supplemental_message": None,
                "confidence": 0.5,
                "reason_code": "unsure",
            }
        )
    )

    # Turn 1: ambiguous -> anchor (attempt 0 -> 1).
    await run_context_turn(db_session, message="嗯", chat_model=ambig_model, event_id="ev-1")
    await db_session.flush()
    anchor = await manager.get_active_topic(
        db_session, SCOPE["tenant_id"], SCOPE["agent_id"], SCOPE["user_id"], SCOPE["channel"],
    )
    assert anchor is not None
    assert anchor.clarification_attempts >= 1

    # Turn 2: ambiguous again -> attempt 1 -> 2 -> safe fallback 'new'.
    result = await run_context_turn(db_session, message="随便", chat_model=ambig_model, event_id="ev-2")
    await db_session.flush()
    # After fallback 'new', a new clean topic is created and anchor is closed.
    assert result["context_status"] == "resolved"
    await db_session.refresh(anchor)
    assert anchor.status == "closed"


@pytest.mark.asyncio
async def test_candidates_older_than_24h_never_appear(db_session, manager):
    """A topic closed >24h ago is not a candidate (not restored, not listed)."""
    old = await _make_closed_topic(db_session, summary="近期话题", age=timedelta(minutes=40))
    stale = await _make_closed_topic(
        db_session, summary="很旧的话题", age=timedelta(hours=25),
    )

    # Only one valid candidate -> '继续' restores it; the stale one stays closed.
    result = await run_context_turn(db_session, message="继续")
    await db_session.flush()
    await db_session.refresh(old)
    await db_session.refresh(stale)
    assert old.status == "active"
    assert stale.status == "closed"
    assert result["topic_id"] == old.id
    assert result["context_status"] == "control"
