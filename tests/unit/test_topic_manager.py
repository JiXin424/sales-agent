"""Tests for Topic Manager — lifecycle, expiry, restore, clarification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select

from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services.topic_manager import (
    MAX_CLARIFICATION_ATTEMPTS,
    TOPIC_IDLE_TIMEOUT,
    TopicManager,
    resolve_clarification,
)
from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T = datetime.now(timezone.utc)


def dt(offset: timedelta = timedelta(0)) -> datetime:
    """Return a fixed UTC datetime offset from T."""
    return T + offset


class StubModel:
    """Stub chat model that returns a canned response."""

    def __init__(self, response: str = ""):
        self.response = response

    async def generate(self, messages, temperature=0, max_tokens=500):
        return self.response


# ===================================================================
# Topic Lifecycle Tests
# ===================================================================


@pytest.mark.asyncio
async def test_active_topic_continues_at_29_minutes(db_session):
    """Topic stays active when idle time < 30 min."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-continues",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=dt(-timedelta(minutes=29)),
        expires_at=dt(timedelta(minutes=1)),
    )
    db_session.add(topic)
    await db_session.flush()

    closed = await mgr.close_if_expired(db_session, topic, now=dt())
    assert not closed
    assert topic.status == "active"


@pytest.mark.asyncio
async def test_active_topic_closes_at_30_minutes(db_session):
    """Topic is closed when idle time >= 30 min."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-closes",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=dt(-timedelta(minutes=30)),
        expires_at=dt(),
    )
    db_session.add(topic)
    await db_session.flush()

    closed = await mgr.close_if_expired(db_session, topic, now=dt())
    assert closed
    assert topic.status == "closed"
    assert topic.closed_at is not None


@pytest.mark.asyncio
async def test_ordinary_message_after_expiry_creates_new_topic(db_session):
    """After topic expires, get_active_topic returns None and create_topic works."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-expired",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=dt(-timedelta(minutes=30)),
        expires_at=dt(),
    )
    db_session.add(topic)
    await db_session.flush()

    # Close expired topic
    await mgr.close_if_expired(db_session, topic, now=dt())

    # No active topic now
    active = await mgr.get_active_topic(db_session, "t1", "a1", "u1", "dingtalk")
    assert active is None

    # Create a new topic
    new_topic = await mgr.create_topic(
        db_session,
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c2",
        summary="新查询",
        current_goal="新查询",
        now=dt(),
    )
    assert new_topic.status == "active"
    assert new_topic.id != topic.id


@pytest.mark.asyncio
async def test_explicit_continue_restores_unique_closed_topic(db_session):
    """A unique closed topic closed <24h ago can be restored."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-restore",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="closed",
        summary="之前的话题",
        current_goal="查询福多多",
        closed_at=dt(-timedelta(hours=1)),
        last_active_at=dt(-timedelta(hours=1, minutes=30)),
        expires_at=dt(-timedelta(hours=1)),
    )
    db_session.add(topic)
    await db_session.flush()

    restorable = await mgr.find_restorable_topics(
        db_session, "t1", "a1", "u1", "dingtalk", now=dt(),
    )
    assert len(restorable) == 1

    restored = await mgr.restore_topic(db_session, topic, now=dt())
    assert restored.status == "active"
    assert restored.closed_at is None


@pytest.mark.asyncio
async def test_multiple_restorable_topics_produces_pending_clarification(db_session):
    """Multiple restorable topics → pending clarification is set."""
    mgr = TopicManager()
    for i in range(2):
        t = ConversationTopic(
            id=f"t-multi-{i}",
            tenant_id="t1",
            agent_id="a1",
            user_id="u1",
            channel="dingtalk",
            conversation_id=f"c{i}",
            status="closed",
            summary=f"话题{i}",
            current_goal=f"目标{i}",
            closed_at=dt(-timedelta(hours=1)),
            last_active_at=dt(-timedelta(hours=2)),
            expires_at=dt(-timedelta(hours=1)),
        )
        db_session.add(t)
    await db_session.flush()

    restorable = await mgr.find_restorable_topics(
        db_session, "t1", "a1", "u1", "dingtalk", now=dt(),
    )
    assert len(restorable) >= 2

    # The orchestrator would call set_pending_clarification
    topic = restorable[0]
    await mgr.set_pending_clarification(
        db_session,
        topic,
        event_id="evt-001",
        original_message="继续刚才",
        candidate_query="延续查询",
    )
    pending = json.loads(topic.pending_clarification_json)
    assert pending["original_message"] == "继续刚才"
    assert pending["candidate_query"] == "延续查询"


@pytest.mark.asyncio
async def test_topic_older_than_24_hours_cannot_restore(db_session):
    """Topics closed >24h ago are not restorable."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-old",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="closed",
        closed_at=dt(-timedelta(hours=25)),
        last_active_at=dt(-timedelta(hours=26)),
        expires_at=dt(-timedelta(hours=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    restorable = await mgr.find_restorable_topics(
        db_session, "t1", "a1", "u1", "dingtalk", now=dt(),
    )
    assert len(restorable) == 0


@pytest.mark.asyncio
async def test_switch_creates_child_topic_with_selected_entities(db_session):
    """Switch closes current topic and creates child with only selected entities."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-switch",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        summary="福多多产品",
        current_goal="查询福多多产品",
        key_entities_json='["福多多", "健康险"]',
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ContextDecision(
        turn_relation="switch",
        standalone_query="查询东方福利网的产品",
        retained_entities=["东方福利网"],
        retracted_goals=[],
        missing_references=[],
        confidence=0.9,
        reason_code="topic_switch",
    )

    result = await mgr.apply_context_decision(db_session, topic, decision, now=dt())

    # Original is closed
    assert topic.status == "closed"
    assert topic.closed_at is not None

    # Child topic carries only selected entities
    assert result.status == "active"
    assert result.parent_topic_id == topic.id
    assert "东方福利网" in result.key_entities_json
    assert "福多多" not in result.key_entities_json


@pytest.mark.asyncio
async def test_revise_keeps_topic_id_and_records_retracted_goals(db_session):
    """Revise keeps same topic ID and records retracted goals."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-revise",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        summary="查福多多产品",
        current_goal="查福多多产品",
        key_entities_json='["福多多"]',
        retracted_goals_json="[]",
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ContextDecision(
        turn_relation="revise",
        standalone_query="查福多多的竞品",
        retained_entities=["福多多"],
        retracted_goals=["查福多多产品"],
        missing_references=[],
        confidence=0.9,
        reason_code="goal_revision",
    )

    result = await mgr.apply_context_decision(db_session, topic, decision, now=dt())

    # Same topic, not closed
    assert result.id == topic.id
    assert result.status == "active"
    goals = json.loads(result.retracted_goals_json)
    assert "查福多多产品" in goals


# ===================================================================
# Clarification Resolver Tests
# ===================================================================


@pytest.mark.asyncio
async def test_exact_continue_maps_with_supplemental():
    """'继续，不过重点看价格' → continue with supplemental text."""
    decision = await resolve_clarification(
        "继续，不过重点看价格", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "continue"
    assert decision.supplemental_message is not None
    assert "重点看价格" in decision.supplemental_message


@pytest.mark.asyncio
async def test_exact_continue_variant():
    """'接着刚才' → continue."""
    decision = await resolve_clarification(
        "接着刚才", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "continue"


@pytest.mark.asyncio
async def test_exact_new_topic():
    """'新问题，我想问东方福利网' → new."""
    decision = await resolve_clarification(
        "新问题，我想问东方福利网", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "new"


@pytest.mark.asyncio
async def test_exact_new_topic_variant():
    """'换个话题' → new."""
    decision = await resolve_clarification(
        "换个话题", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "new"


@pytest.mark.asyncio
async def test_exact_cancel():
    """'取消' → cancel."""
    decision = await resolve_clarification(
        "取消", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "cancel"


@pytest.mark.asyncio
async def test_exact_cancel_variant():
    """'算了' → cancel."""
    decision = await resolve_clarification(
        "算了", chat_model=None, attempt_count=0,
    )
    assert decision.resolution == "cancel"


@pytest.mark.asyncio
async def test_replacement_message_via_model():
    """A complete replacement message resolves via model as 'replace'."""
    stub = StubModel(
        '{"resolution": "replace", "replacement_text": "查东方福利网的产品", "confidence": 0.9}',
    )
    decision = await resolve_clarification(
        "查东方福利网的产品", chat_model=stub, attempt_count=0,
    )
    assert decision.resolution == "replace"
    assert decision.replacement_text == "查东方福利网的产品"


@pytest.mark.asyncio
async def test_second_ambiguous_answer_defaults_new():
    """Attempt count >= MAX → 'new' without calling model."""
    stub = StubModel("")  # Would fail if called
    decision = await resolve_clarification(
        "随便", chat_model=stub, attempt_count=MAX_CLARIFICATION_ATTEMPTS,
    )
    assert decision.resolution == "new"


@pytest.mark.asyncio
async def test_non_exact_message_uses_model(db_session):
    """A message that matches no exact command falls through to the model."""
    stub = StubModel(
        '{"resolution": "continue", "supplemental_message": "查福多多竞品", "confidence": 0.85}',
    )
    decision = await resolve_clarification(
        "查福多多竞品", chat_model=stub, attempt_count=0,
    )
    assert decision.resolution == "continue"
    assert decision.supplemental_message == "查福多多竞品"


# ===================================================================
# Clarification Loop / Pending Tests
# ===================================================================


@pytest.mark.asyncio
async def test_pending_json_retains_original_message_and_candidate_query(db_session):
    """set_pending_clarification stores original_message and candidate_query."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-pending-store",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=dt(),
        expires_at=dt(timedelta(minutes=30)),
    )
    db_session.add(topic)
    await db_session.flush()

    await mgr.set_pending_clarification(
        db_session,
        topic,
        event_id="evt-001",
        original_message="继续刚才的具体问题",
        candidate_query="之前查询福多多产品",
    )

    pending = json.loads(topic.pending_clarification_json)
    assert pending["event_id"] == "evt-001"
    assert pending["original_message"] == "继续刚才的具体问题"
    assert pending["candidate_query"] == "之前查询福多多产品"
    assert "created_at" in pending


@pytest.mark.asyncio
async def test_duplicate_event_id_pending_only_once(db_session):
    """Same event_id does not overwrite existing pending data."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-pending-dedup",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=dt(),
        expires_at=dt(timedelta(minutes=30)),
    )
    db_session.add(topic)
    await db_session.flush()

    # First call
    await mgr.set_pending_clarification(
        db_session,
        topic,
        event_id="evt-001",
        original_message="继续刚才",
        candidate_query="原查询",
    )
    # Second call with same event_id but different data
    await mgr.set_pending_clarification(
        db_session,
        topic,
        event_id="evt-001",
        original_message="不应该被更新",
        candidate_query="不同查询",
    )

    pending = json.loads(topic.pending_clarification_json)
    assert pending["original_message"] == "继续刚才"
    assert pending["candidate_query"] == "原查询"


@pytest.mark.asyncio
async def test_cancel_pending_clears_state(db_session):
    """cancel_pending clears pending_clarification_json and resets attempts."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-cancel-pending",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        pending_clarification_json=json.dumps({
            "event_id": "evt-001",
            "original_message": "test",
            "candidate_query": "query",
        }),
        clarification_attempts=1,
        last_active_at=dt(),
        expires_at=dt(timedelta(minutes=30)),
    )
    db_session.add(topic)
    await db_session.flush()

    await mgr.cancel_pending(db_session, topic)

    assert topic.pending_clarification_json is None
    assert topic.clarification_attempts == 0


@pytest.mark.asyncio
async def test_resolve_pending_continue_updates_goal(db_session):
    """resolve_pending with 'continue' clears pending and updates goal."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-resolve-continue",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        pending_clarification_json=json.dumps({
            "event_id": "evt-001",
            "original_message": "继续",
            "candidate_query": "q",
        }),
        current_goal="旧目标",
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ClarificationDecision(
        resolution="continue",
        supplemental_message="查福多多竞品",
        confidence=0.9,
    )
    result = await mgr.resolve_pending(db_session, topic, decision, now=dt())

    assert result.pending_clarification_json is None
    assert result.clarification_attempts == 0
    assert result.current_goal == "查福多多竞品"


@pytest.mark.asyncio
async def test_resolve_pending_new_closes_topic(db_session):
    """resolve_pending with 'new' clears pending and closes topic."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-resolve-new",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        pending_clarification_json=json.dumps({
            "event_id": "evt-001",
            "original_message": "新问题",
            "candidate_query": "q",
        }),
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ClarificationDecision(resolution="new", confidence=0.9)
    result = await mgr.resolve_pending(db_session, topic, decision, now=dt())

    assert result.pending_clarification_json is None
    assert result.status == "closed"
    assert result.closed_at is not None


@pytest.mark.asyncio
async def test_resolve_pending_replace_updates_goal(db_session):
    """resolve_pending with 'replace' clears pending and replaces goal."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-resolve-replace",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        pending_clarification_json=json.dumps({
            "event_id": "evt-001",
            "original_message": "查东方福利网",
            "candidate_query": "q",
        }),
        current_goal="旧目标",
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ClarificationDecision(
        resolution="replace",
        replacement_text="查东方福利网的产品",
        confidence=0.9,
    )
    result = await mgr.resolve_pending(db_session, topic, decision, now=dt())

    assert result.pending_clarification_json is None
    assert result.current_goal == "查东方福利网的产品"


@pytest.mark.asyncio
async def test_resolve_pending_cancel_closes_topic(db_session):
    """resolve_pending with 'cancel' clears pending and closes topic."""
    mgr = TopicManager()
    topic = ConversationTopic(
        id="t-resolve-cancel",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        pending_clarification_json=json.dumps({
            "event_id": "evt-001",
            "original_message": "算了",
            "candidate_query": "q",
        }),
        last_active_at=dt(-timedelta(minutes=5)),
        expires_at=dt(timedelta(minutes=25)),
    )
    db_session.add(topic)
    await db_session.flush()

    decision = ClarificationDecision(resolution="cancel", confidence=0.9)
    result = await mgr.resolve_pending(db_session, topic, decision, now=dt())

    assert result.pending_clarification_json is None
    assert result.status == "closed"
    assert result.closed_at is not None


# ===================================================================
# Transaction / Scope Tests
# ===================================================================


@pytest.mark.asyncio
async def test_scope_filters_by_tenant_agent_user_channel(db_session):
    """get_active_topic scoped to tenant+agent+user+channel."""
    mgr = TopicManager()
    now_dt = dt()

    # Topic for tenant t1, agent a1, user u1, channel dingtalk
    t1 = ConversationTopic(
        id="t-scope-1",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=now_dt,
        expires_at=now_dt + timedelta(minutes=30),
    )
    # Topic for same scope but different channel (local)
    t2 = ConversationTopic(
        id="t-scope-2",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        channel="local",
        conversation_id="c2",
        status="active",
        last_active_at=now_dt,
        expires_at=now_dt + timedelta(minutes=30),
    )
    db_session.add_all([t1, t2])
    await db_session.flush()

    # Query should only return the dingtalk one
    found = await mgr.get_active_topic(db_session, "t1", "a1", "u1", "dingtalk")
    assert found is not None
    assert found.id == "t-scope-1"
