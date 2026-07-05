from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class ConversationTopic(TimestampMixin, Base):
    __tablename__ = "conversation_topics"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    parent_topic_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_entities_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    current_goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_constraints_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    retracted_goals_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pending_clarification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_conversation_topic_active_scope",
            "tenant_id", "agent_id", "user_id", "channel",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
