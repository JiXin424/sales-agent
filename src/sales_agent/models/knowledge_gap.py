"""知识缺口追踪模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class KnowledgeGap(TimestampMixin, Base):
    """知识缺口追踪。

    从实际使用中发现的知识缺失，追踪从发现到补充验证的完整生命周期。
    """

    __tablename__ = "knowledge_gaps"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    source_conversation_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    source_feedback_id: Mapped[str] = mapped_column(Text, nullable=True)
    linked_document_id: Mapped[str] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open"
    )  # open / document_needed / uploaded / verified / ignored
    priority: Mapped[str] = mapped_column(
        Text, nullable=False, default="medium"
    )  # low / medium / high / critical
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_knowledge_gaps_tenant_status", "tenant_id", "status"),
    )
