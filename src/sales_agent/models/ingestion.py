"""知识导入任务模型。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class IngestionJob(TimestampMixin, Base):
    """知识导入任务记录。

    状态：queued / running / completed / completed_with_errors / failed。
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_file_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued"
    )  # queued / running / completed / completed_with_errors / failed
    documents_seen: Mapped[int] = mapped_column(nullable=False, default=0)
    documents_ingested: Mapped[int] = mapped_column(nullable=False, default=0)
    chunks_created: Mapped[int] = mapped_column(nullable=False, default=0)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    errors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_summary: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # --- Ontology (Neo4j knowledge engine) ---
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    engine: Mapped[str] = mapped_column(Text, nullable=False, default="legacy_rag")
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    entities_created: Mapped[int] = mapped_column(nullable=False, default=0)
    entities_merged: Mapped[int] = mapped_column(nullable=False, default=0)
    facts_created: Mapped[int] = mapped_column(nullable=False, default=0)
    facts_active: Mapped[int] = mapped_column(nullable=False, default=0)
    facts_pending_review: Mapped[int] = mapped_column(nullable=False, default=0)
    facts_rejected: Mapped[int] = mapped_column(nullable=False, default=0)
    conflicts_created: Mapped[int] = mapped_column(nullable=False, default=0)
