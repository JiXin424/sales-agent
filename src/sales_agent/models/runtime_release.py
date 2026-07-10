"""Immutable release and runtime binding models.

OptimizationRelease, AgentRuntimeBinding, and ReleaseEvent.
"""

from sqlalchemy import Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class OptimizationRelease(TimestampMixin, Base):
    """Immutable release manifest combining knowledge, retrieval, router, prompt,
    model snapshot, graph definition, and code revision references."""

    __tablename__ = "optimization_releases"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    release_number: Mapped[int] = mapped_column(nullable=False, default=1)
    parent_release_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_of_release_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    router_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    graph_definition_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    iteration_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    rolled_back_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "release_number", name="uq_rel_tenant_agent_number"),
        Index("ix_rel_tenant_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<OptimizationRelease(id={self.id}, tenant={self.tenant_id}, rel={self.release_number})>"


class AgentRuntimeBinding(TimestampMixin, Base):
    """Atomic active-release pointer per tenant/Agent with optimistic locking."""

    __tablename__ = "agent_runtime_bindings"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    active_release_id: Mapped[str] = mapped_column(Text, nullable=False)
    previous_release_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    lock_version: Mapped[int] = mapped_column(nullable=False, default=1)
    activated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", name="uq_runtime_binding_tenant_agent"),
    )

    def __repr__(self) -> str:
        return f"<AgentRuntimeBinding(tenant={self.tenant_id}, agent={self.agent_id}, lock={self.lock_version})>"


class ReleaseEvent(TimestampMixin, Base):
    """Immutable audit event for approval, publication, canary, and rollback."""

    __tablename__ = "release_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    release_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    status_transition: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_rel_ev_tenant_release", "tenant_id", "release_id"),
    )

    def __repr__(self) -> str:
        return f"<ReleaseEvent(release={self.release_id}, event={self.event_type})>"
