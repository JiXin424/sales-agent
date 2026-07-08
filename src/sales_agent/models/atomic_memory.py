from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class AtomicMemory(TimestampMixin, Base):
    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    subject_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence_band: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    supersedes_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_agent_memories_scope_status",
            "tenant_id", "agent_id", "subject_type", "subject_id", "status",
        ),
        Index(
            "ix_agent_memories_scope_key",
            "tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key",
        ),
        Index("ix_agent_memories_expiry", "status", "expires_at"),
        Index("ix_agent_memories_source_message", "tenant_id", "source_message_ids_json"),
        Index(
            "uq_agent_memory_active_single_value",
            "tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class MemoryOutboxJob(TimestampMixin, Base):
    __tablename__ = "memory_outbox"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False, default="infer_candidates")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", "operation", name="uq_memory_outbox_event_operation"),
        Index("ix_memory_outbox_poll", "status", "available_at"),
        Index("ix_memory_outbox_scope", "tenant_id", "agent_id", "user_id"),
    )


class MemoryAuditEvent(TimestampMixin, Base):
    __tablename__ = "memory_audit_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    memory_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
