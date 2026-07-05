"""ConversationTopic model tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from sales_agent.core.database import Base
from sales_agent.models.conversation_topic import ConversationTopic


def test_topic_table_is_registered():
    """conversation_topics must be in Base.metadata."""
    tables = set(Base.metadata.tables)
    assert "conversation_topics" in tables, "conversation_topics table not registered"


def test_topic_columns_exist():
    """Verify key columns are present on the model."""
    columns = Base.metadata.tables["conversation_topics"].c
    expected = {
        "id", "tenant_id", "agent_id", "user_id", "channel",
        "conversation_id", "parent_topic_id", "status",
        "summary", "key_entities_json", "current_goal",
        "active_constraints_json", "retracted_goals_json",
        "pending_clarification_json", "clarification_attempts",
        "last_active_at", "expires_at", "closed_at",
        "created_at", "updated_at",
    }
    actual = set(columns.keys())
    missing = expected - actual
    assert not missing, f"Missing columns: {missing}"


@pytest.mark.asyncio
async def test_topic_round_trip(db_session, active_agent):
    now = datetime.now(timezone.utc)
    topic = ConversationTopic(
        id="topic-1",
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        summary="讨论福多多产品",
        key_entities_json='["福多多"]',
        current_goal="查询产品",
        last_active_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(topic)
    await db_session.flush()
    loaded = await db_session.scalar(select(ConversationTopic).where(ConversationTopic.id == "topic-1"))
    assert loaded is not None
    assert loaded.current_goal == "查询产品"
    assert loaded.status == "active"


@pytest.mark.asyncio
async def test_topic_nullable_fields_default_to_none(db_session, active_agent):
    """parent_topic_id, pending_clarification_json, closed_at may be None."""
    now = datetime.now(timezone.utc)
    topic = ConversationTopic(
        id="topic-nullable",
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(topic)
    await db_session.flush()
    loaded = await db_session.scalar(select(ConversationTopic).where(ConversationTopic.id == "topic-nullable"))
    assert loaded.parent_topic_id is None
    assert loaded.pending_clarification_json is None
    assert loaded.closed_at is None


@pytest.mark.asyncio
async def test_topic_active_unique_constraint(db_session, active_agent):
    """Only one active topic per (tenant, agent, user, channel)."""
    now = datetime.now(timezone.utc)
    t1 = ConversationTopic(
        id="topic-uq-1",
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        last_active_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(t1)
    await db_session.flush()

    t2 = ConversationTopic(
        id="topic-uq-2",
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        user_id="u1",
        channel="dingtalk",
        conversation_id="c2",
        status="active",
        last_active_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(t2)
    with pytest.raises(Exception):
        await db_session.flush()
