"""Versioned knowledge fact model for oracle lookup and question generation.

Facts are extracted from document revisions, normalized, deduplicated via
canonical hash, and conflict-tracked across versions.
"""

from sqlalchemy import Text, Float, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class KnowledgeFact(TimestampMixin, Base):
    """A structured fact extracted from a knowledge document revision.

    Each fact has subject/predicate/normalized_object and is linked to its
    source document revision.  Facts with the same canonical hash across
    different revisions are deduplicated; conflicting facts produce review
    items.
    """

    __tablename__ = "knowledge_facts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    knowledge_version_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_revision_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured fact fields
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    qualifiers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Evidence
    evidence_offsets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    effective_start: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_end: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extraction metadata
    extractor_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dedup and conflict
    canonical_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    conflict_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="unique"
    )  # unique / conflicting / resolved

    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical_hash", name="uq_kf_tenant_hash"),
        Index("ix_kf_tenant_version", "tenant_id", "knowledge_version_id"),
        Index("ix_kf_tenant_doc_rev", "tenant_id", "document_revision_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeFact(subj={self.subject}, pred={self.predicate}, conflict={self.conflict_status})>"
