"""文档和文档块模型。"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from pgvector.sqlalchemy import Vector

from .base import Base, TimestampMixin, generate_id


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, tenant_id={self.tenant_id}, title={self.title})>"


class SourceFile(TimestampMixin, Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_markdown_path: Mapped[str] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    def __repr__(self) -> str:
        return f"<SourceFile(id={self.id}, tenant_id={self.tenant_id}, file={self.original_filename})>"


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # pgvector 向量列，1024 维
    embedding = mapped_column(Vector(1024), nullable=True)
    # Version-pinned columns for multi-version knowledge isolation
    knowledge_version_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    document_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    chunker_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_config_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<DocumentChunk(id={self.id}, tenant_id={self.tenant_id}, doc={self.document_id})>"
