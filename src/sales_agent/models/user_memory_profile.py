from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class UserMemoryProfile(TimestampMixin, Base):
    __tablename__ = "user_memory_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ready")
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_map_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_memory_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)

    __table_args__ = (
        Index("ix_user_memory_profiles_scope", "tenant_id", "agent_id", "user_id"),
        Index(
            "uq_user_memory_profile_current_scope",
            "tenant_id", "agent_id", "user_id",
            unique=True,
            postgresql_where=text("status IN ('ready', 'rebuilding', 'degraded')"),
        ),
    )


class UserProfileRebuildJob(TimestampMixin, Base):
    __tablename__ = "user_profile_rebuild_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "user_id", "reason", "source_memory_id",
            name="uq_user_profile_rebuild_scope_reason",
        ),
        Index("ix_user_profile_rebuild_jobs_poll", "status", "available_at"),
        Index("ix_user_profile_rebuild_jobs_scope", "tenant_id", "agent_id", "user_id"),
    )
