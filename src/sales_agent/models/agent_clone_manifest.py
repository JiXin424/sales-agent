"""Agent 克隆清单 — 记录复制/引用/重置/跳过的资源，可审计。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentCloneManifest(TimestampMixin, Base):
    """克隆操作的审计清单。

    options_json: 用户在克隆向导中选择的选项。
    copied_resources_json: 完整复制并独立化的资源。
    referenced_resources_json: 以引用方式复用的资源（如 default 知识）。
    reset_resources_json: 重置（未复制）的运行历史类资源。
    skipped_resources_json: 显式跳过的资源。
    """

    __tablename__ = "agent_clone_manifests"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    source_agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target_agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    copied_resources_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    referenced_resources_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reset_resources_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    skipped_resources_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_agent_clone_manifests_source", "source_agent_id"),
        Index("ix_agent_clone_manifests_target", "target_agent_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentCloneManifest(source={self.source_agent_id}, "
            f"target={self.target_agent_id})>"
        )
