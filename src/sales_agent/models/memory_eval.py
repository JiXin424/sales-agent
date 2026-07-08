"""ORM models for production memory-eval operations (Spec 4 §8, §9).

- ``MemoryEvalTraceRecord``: a sampled production trace kept under restricted
  retention (hashed scope, no plaintext identifiers) — Spec 4 §9.2.
- ``PromotedRegression``: an anonymized regression scenario promoted from a
  reviewed trace (Spec 4 §9.4), with status ``draft`` → ``reviewed`` →
  ``committed``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class MemoryEvalTraceRecord(TimestampMixin, Base):
    """A sampled production trace retained for memory evaluation.

    Identifiers are protected: ``scope_hash`` holds a hashed scope token rather
    than plaintext tenant/user ids, and ``thread_id`` follows the same hashed
    convention used by the eval trace capture path.
    """

    __tablename__ = "memory_eval_traces"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scope_hash: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    retention: Mapped[str] = mapped_column(
        Text, nullable=False, default="restricted"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="sampled")
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_memory_eval_traces_tenant_status", "tenant_id", "status"),
    )


class PromotedRegression(TimestampMixin, Base):
    """An anonymized regression scenario promoted from a sampled trace.

    Lifecycle: ``draft`` → ``reviewed`` → ``committed``. ``anonymized`` must be
    true before a scenario is committed to the regression suite.
    """

    __tablename__ = "promoted_regressions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    anonymized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["MemoryEvalTraceRecord", "PromotedRegression"]
