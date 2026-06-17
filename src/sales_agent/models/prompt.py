"""Prompt 版本管理模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class PromptVersion(TimestampMixin, Base):
    """Prompt 版本记录。

    每个 tenant + task_type 最多一个 active 版本。
    状态流转：draft → active → archived。
    """

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="draft"
    )  # draft / active / archived
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index(
            "ix_prompt_versions_tenant_task_status",
            "tenant_id",
            "task_type",
            "status",
        ),
    )
