"""Add governed long-term atomic memory tables.

Revision ID: 0013_governed_long_term_memory
Revises: 0012_backfill_skipped_columns
Create Date: 2026-07-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_governed_long_term_memory"
down_revision: Union[str, None] = "0012_backfill_skipped_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default=sa.text("'user'")),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_conversation_id", sa.Text(), nullable=False),
        sa.Column("source_message_ids_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("confidence_band", sa.Text(), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("sensitivity", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("supersedes_id", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memories_tenant_id", "agent_memories", ["tenant_id"])
    op.create_index("ix_agent_memories_agent_id", "agent_memories", ["agent_id"])
    op.create_index("ix_agent_memories_subject_id", "agent_memories", ["subject_id"])
    op.create_index("ix_agent_memories_supersedes_id", "agent_memories", ["supersedes_id"])
    op.create_index(
        "ix_agent_memories_scope_status",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "status"],
    )
    op.create_index(
        "ix_agent_memories_scope_key",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key"],
    )
    op.create_index("ix_agent_memories_expiry", "agent_memories", ["status", "expires_at"])
    op.create_index("ix_agent_memories_source_message", "agent_memories", ["tenant_id", "source_message_ids_json"])
    op.create_index(
        "uq_agent_memory_active_single_value",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "memory_outbox",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False, server_default=sa.text("'infer_candidates'")),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_id", "operation", name="uq_memory_outbox_event_operation"),
    )
    op.create_index("ix_memory_outbox_tenant_id", "memory_outbox", ["tenant_id"])
    op.create_index("ix_memory_outbox_agent_id", "memory_outbox", ["agent_id"])
    op.create_index("ix_memory_outbox_user_id", "memory_outbox", ["user_id"])
    op.create_index("ix_memory_outbox_poll", "memory_outbox", ["status", "available_at"])
    op.create_index("ix_memory_outbox_scope", "memory_outbox", ["tenant_id", "agent_id", "user_id"])

    op.create_table(
        "memory_audit_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_audit_events_tenant_id", "memory_audit_events", ["tenant_id"])
    op.create_index("ix_memory_audit_events_agent_id", "memory_audit_events", ["agent_id"])
    op.create_index("ix_memory_audit_events_user_id", "memory_audit_events", ["user_id"])
    op.create_index("ix_memory_audit_events_memory_id", "memory_audit_events", ["memory_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_audit_events_memory_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_user_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_agent_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_tenant_id", table_name="memory_audit_events")
    op.drop_table("memory_audit_events")
    op.drop_index("ix_memory_outbox_scope", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_poll", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_user_id", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_agent_id", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_tenant_id", table_name="memory_outbox")
    op.drop_table("memory_outbox")
    op.drop_index("uq_agent_memory_active_single_value", table_name="agent_memories")
    op.drop_index("ix_agent_memories_source_message", table_name="agent_memories")
    op.drop_index("ix_agent_memories_expiry", table_name="agent_memories")
    op.drop_index("ix_agent_memories_scope_key", table_name="agent_memories")
    op.drop_index("ix_agent_memories_scope_status", table_name="agent_memories")
    op.drop_index("ix_agent_memories_supersedes_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_subject_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_agent_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_tenant_id", table_name="agent_memories")
    op.drop_table("agent_memories")
