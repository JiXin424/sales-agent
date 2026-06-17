"""运维告警模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AlertRule(TimestampMixin, Base):
    """告警规则。

    定义监控指标的条件阈值，触发时生成告警记录。
    """

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # model_failures / dingtalk_failures / ingestion_failures / slow_p95 / high_error_rate / high_negative_feedback / retrieval_misses
    condition: Mapped[str] = mapped_column(
        Text, nullable=False, default="gt"
    )  # gt / gte / lt / lte
    threshold: Mapped[float] = mapped_column(nullable=False)
    window_minutes: Mapped[int] = mapped_column(nullable=False, default=60)
    severity: Mapped[str] = mapped_column(
        Text, nullable=False, default="warning"
    )  # info / warning / critical
    enabled: Mapped[str] = mapped_column(Text, nullable=False, default="true")  # "true" / "false"
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Alert(TimestampMixin, Base):
    """告警记录。

    规则触发后生成，支持确认和解决生命周期。
    """

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    alert_rule_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        Text, nullable=False, default="warning"
    )  # info / warning / critical
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    threshold_value: Mapped[float] = mapped_column(nullable=False)
    observed_value: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active"
    )  # active / acknowledged / resolved
    first_seen_at: Mapped[str] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_alerts_tenant_status", "tenant_id", "status"),
    )
