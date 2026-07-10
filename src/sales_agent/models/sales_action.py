"""销售动作卡片与提醒相关的数据库模型。

包含：动作卡片、提醒、投递记录、事件流水。
时间戳（created_at/updated_at）由 TimestampMixin 提供，此处仅声明领域列。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class SalesActionCard(TimestampMixin, Base):
    __tablename__ = "sales_action_cards"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="dingtalk")
    dingtalk_user_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    topic_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    source_event_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    context_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    agent_advice: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Plan → Act → Observe → Replan (pursuit-loop) ──
    success_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    pursuit_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_met_signal: Mapped[bool | None] = mapped_column(nullable=True)
    outcome_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_sales_action_cards_scope_status", "tenant_id", "agent_id", "user_id", "status"),
        Index("ix_sales_action_cards_scheduled", "status", "scheduled_at"),
        Index("ix_sales_action_cards_source_event", "tenant_id", "source_event_id"),
        Index("ix_sales_action_cards_pursuit_goal", "tenant_id", "pursuit_goal", "status"),
    )


class SalesActionReminder(TimestampMixin, Base):
    __tablename__ = "sales_action_reminders"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminder_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="scheduled")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_sales_action_reminders_idempotency"),
        Index("ix_sales_action_reminders_due", "status", "remind_at", "next_attempt_at"),
        Index("ix_sales_action_reminders_scope", "tenant_id", "agent_id", "user_id"),
    )


class SalesActionDelivery(TimestampMixin, Base):
    __tablename__ = "sales_action_deliveries"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    reminder_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="dingtalk")
    delivery_type: Mapped[str] = mapped_column(Text, nullable=False)
    dingtalk_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_instance_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sales_action_deliveries_scope", "tenant_id", "agent_id", "user_id", "status"),
    )


class SalesActionEvent(TimestampMixin, Base):
    __tablename__ = "sales_action_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_sales_action_events_action", "action_id", "created_at"),
        Index("ix_sales_action_events_scope", "tenant_id", "agent_id", "user_id", "event_type"),
    )
