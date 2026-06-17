"""Agent 知识作用域 — 决定检索范围。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentKnowledgeScope(TimestampMixin, Base):
    """Agent 的知识可见范围。

    mode:
      tenant_all          — 沿用 tenant 全量文档（默认，兼容旧行为）
      document_subset     — 仅指定 document_ids
      source_file_subset  — 仅指定 source_file_ids 派生的文档
    """

    __tablename__ = "agent_knowledge_scopes"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="tenant_all"
    )  # tenant_all / document_subset / source_file_subset
    document_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_file_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<AgentKnowledgeScope(id={self.id}, agent={self.agent_id}, mode={self.mode})>"
