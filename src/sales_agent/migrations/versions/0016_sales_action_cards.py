"""Add sales action cards, reminders, deliveries, and events tables.

Revision ID: 0016_sales_action_cards
Revises: 0015_memory_eval_operations
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_sales_action_cards"
down_revision: Union[str, None] = "0015_memory_eval_operations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- sales_action_cards: 销售动作卡片 ---
    op.create_table(
        "sales_action_cards",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False, server_default=sa.text("'dingtalk'")),
        sa.Column("dingtalk_user_id", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=True),
        sa.Column("source_event_id", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("action_type", sa.Text(), nullable=False, server_default=sa.text("'other'")),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("context_snapshot_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("agent_advice", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_action_cards_tenant_id", "sales_action_cards", ["tenant_id"])
    op.create_index("ix_sales_action_cards_agent_id", "sales_action_cards", ["agent_id"])
    op.create_index("ix_sales_action_cards_user_id", "sales_action_cards", ["user_id"])
    op.create_index("ix_sales_action_cards_dingtalk_user_id", "sales_action_cards", ["dingtalk_user_id"])
    op.create_index("ix_sales_action_cards_conversation_id", "sales_action_cards", ["conversation_id"])
    op.create_index("ix_sales_action_cards_topic_id", "sales_action_cards", ["topic_id"])
    op.create_index("ix_sales_action_cards_source_event_id", "sales_action_cards", ["source_event_id"])
    op.create_index(
        "ix_sales_action_cards_scope_status",
        "sales_action_cards",
        ["tenant_id", "agent_id", "user_id", "status"],
    )
    op.create_index("ix_sales_action_cards_scheduled", "sales_action_cards", ["status", "scheduled_at"])
    op.create_index(
        "ix_sales_action_cards_source_event", "sales_action_cards", ["tenant_id", "source_event_id"]
    )

    # --- sales_action_reminders: 提醒（幂等键防重复） ---
    op.create_table(
        "sales_action_reminders",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reminder_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'scheduled'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_action_reminders_idempotency"),
    )
    op.create_index("ix_sales_action_reminders_action_id", "sales_action_reminders", ["action_id"])
    op.create_index("ix_sales_action_reminders_tenant_id", "sales_action_reminders", ["tenant_id"])
    op.create_index("ix_sales_action_reminders_agent_id", "sales_action_reminders", ["agent_id"])
    op.create_index("ix_sales_action_reminders_user_id", "sales_action_reminders", ["user_id"])
    op.create_index(
        "ix_sales_action_reminders_due",
        "sales_action_reminders",
        ["status", "remind_at", "next_attempt_at"],
    )
    op.create_index(
        "ix_sales_action_reminders_scope", "sales_action_reminders", ["tenant_id", "agent_id", "user_id"]
    )

    # --- sales_action_deliveries: 投递记录 ---
    op.create_table(
        "sales_action_deliveries",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=True),
        sa.Column("reminder_id", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False, server_default=sa.text("'dingtalk'")),
        sa.Column("delivery_type", sa.Text(), nullable=False),
        sa.Column("dingtalk_message_id", sa.Text(), nullable=True),
        sa.Column("card_instance_id", sa.Text(), nullable=True),
        sa.Column("rendered_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_action_deliveries_action_id", "sales_action_deliveries", ["action_id"])
    op.create_index("ix_sales_action_deliveries_reminder_id", "sales_action_deliveries", ["reminder_id"])
    op.create_index("ix_sales_action_deliveries_tenant_id", "sales_action_deliveries", ["tenant_id"])
    op.create_index("ix_sales_action_deliveries_agent_id", "sales_action_deliveries", ["agent_id"])
    op.create_index("ix_sales_action_deliveries_user_id", "sales_action_deliveries", ["user_id"])
    op.create_index(
        "ix_sales_action_deliveries_scope",
        "sales_action_deliveries",
        ["tenant_id", "agent_id", "user_id", "status"],
    )

    # --- sales_action_events: 事件流水 ---
    op.create_table(
        "sales_action_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_payload_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_action_events_action_id", "sales_action_events", ["action_id"])
    op.create_index("ix_sales_action_events_tenant_id", "sales_action_events", ["tenant_id"])
    op.create_index("ix_sales_action_events_agent_id", "sales_action_events", ["agent_id"])
    op.create_index("ix_sales_action_events_user_id", "sales_action_events", ["user_id"])
    op.create_index("ix_sales_action_events_action", "sales_action_events", ["action_id", "created_at"])
    op.create_index(
        "ix_sales_action_events_scope",
        "sales_action_events",
        ["tenant_id", "agent_id", "user_id", "event_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_action_events_scope", table_name="sales_action_events")
    op.drop_index("ix_sales_action_events_action", table_name="sales_action_events")
    op.drop_index("ix_sales_action_events_user_id", table_name="sales_action_events")
    op.drop_index("ix_sales_action_events_agent_id", table_name="sales_action_events")
    op.drop_index("ix_sales_action_events_tenant_id", table_name="sales_action_events")
    op.drop_index("ix_sales_action_events_action_id", table_name="sales_action_events")
    op.drop_table("sales_action_events")

    op.drop_index("ix_sales_action_deliveries_scope", table_name="sales_action_deliveries")
    op.drop_index("ix_sales_action_deliveries_user_id", table_name="sales_action_deliveries")
    op.drop_index("ix_sales_action_deliveries_agent_id", table_name="sales_action_deliveries")
    op.drop_index("ix_sales_action_deliveries_tenant_id", table_name="sales_action_deliveries")
    op.drop_index("ix_sales_action_deliveries_reminder_id", table_name="sales_action_deliveries")
    op.drop_index("ix_sales_action_deliveries_action_id", table_name="sales_action_deliveries")
    op.drop_table("sales_action_deliveries")

    op.drop_index("ix_sales_action_reminders_scope", table_name="sales_action_reminders")
    op.drop_index("ix_sales_action_reminders_due", table_name="sales_action_reminders")
    op.drop_index("ix_sales_action_reminders_user_id", table_name="sales_action_reminders")
    op.drop_index("ix_sales_action_reminders_agent_id", table_name="sales_action_reminders")
    op.drop_index("ix_sales_action_reminders_tenant_id", table_name="sales_action_reminders")
    op.drop_index("ix_sales_action_reminders_action_id", table_name="sales_action_reminders")
    op.drop_table("sales_action_reminders")

    op.drop_index("ix_sales_action_cards_source_event", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_scheduled", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_scope_status", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_source_event_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_topic_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_conversation_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_dingtalk_user_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_user_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_agent_id", table_name="sales_action_cards")
    op.drop_index("ix_sales_action_cards_tenant_id", table_name="sales_action_cards")
    op.drop_table("sales_action_cards")
