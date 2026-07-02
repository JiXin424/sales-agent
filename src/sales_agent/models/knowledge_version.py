"""Immutable knowledge and configuration version models.

DocumentRevision, KnowledgeVersion, KnowledgeVersionDocument,
RetrievalProfile, and RouterProfile.
"""

from sqlalchemy import Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class DocumentRevision(TimestampMixin, Base):
    """Immutable revision of a logical document's content."""

    __tablename__ = "document_revisions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_number: Mapped[int] = mapped_column(nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    change_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_start: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    creator_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "revision_number", name="uq_doc_rev_tenant_doc_number"),
        Index("ix_doc_rev_tenant_document", "tenant_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<DocumentRevision(id={self.id}, doc={self.document_id}, rev={self.revision_number})>"


class KnowledgeVersion(TimestampMixin, Base):
    """Immutable snapshot of a tenant/Agent's knowledge corpus."""

    __tablename__ = "knowledge_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_number: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(nullable=False, default=0)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    retired_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "version_number", name="uq_kv_tenant_agent_version"),
        Index("ix_kv_tenant_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeVersion(id={self.id}, tenant={self.tenant_id}, v={self.version_number})>"


class KnowledgeVersionDocument(TimestampMixin, Base):
    """Join table: maps a knowledge version to one revision of each included document."""

    __tablename__ = "knowledge_version_documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    knowledge_version_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_version_id", "document_id",
            name="uq_kvd_version_document",
        ),
        Index("ix_kvd_tenant_version", "tenant_id", "knowledge_version_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeVersionDocument(kv={self.knowledge_version_id}, doc={self.document_id})>"


class RetrievalProfile(TimestampMixin, Base):
    """Versioned retrieval configuration profile."""

    __tablename__ = "retrieval_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    version_number: Mapped[int] = mapped_column(nullable=False, default=1)
    parent_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    retrieval_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_k: Mapped[int] = mapped_column(nullable=False, default=5)
    candidate_k: Mapped[int] = mapped_column(nullable=False, default=30)
    min_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    keyword_weight: Mapped[float] = mapped_column(nullable=False, default=0.3)
    rrf_constant: Mapped[int] = mapped_column(nullable=False, default=60)
    reranker: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_rewrite_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    tenant_synonyms_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    chunk_settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    config_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "version_number", name="uq_rp_tenant_agent_version"),
        Index("ix_rp_tenant_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<RetrievalProfile(id={self.id}, tenant={self.tenant_id}, v={self.version_number})>"


class RouterProfile(TimestampMixin, Base):
    """Versioned router configuration profile."""

    __tablename__ = "router_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    version_number: Mapped[int] = mapped_column(nullable=False, default=1)
    parent_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    prompt_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    confidence_threshold: Mapped[float] = mapped_column(nullable=False, default=0.6)
    knowledge_trigger_rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    config_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "version_number", name="uq_rtp_tenant_agent_version"),
        Index("ix_rtp_tenant_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<RouterProfile(id={self.id}, tenant={self.tenant_id}, v={self.version_number})>"
