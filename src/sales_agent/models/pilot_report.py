"""Pilot 报告模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class PilotReport(TimestampMixin, Base):
    """Pilot 阶段报告。

    支持周报、月报和自定义时间范围的报告生成。
    包含结构化 JSON 和 Markdown 两种格式。
    """

    __tablename__ = "pilot_reports"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    report_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="weekly"
    )  # weekly / monthly / custom
    start_date: Mapped[str] = mapped_column(Text, nullable=False)
    end_date: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="generating"
    )  # generating / completed / failed
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    markdown_content: Mapped[str] = mapped_column(Text, nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_pilot_reports_tenant_type", "tenant_id", "report_type"),
    )
