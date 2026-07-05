"""Add conversation topic memory.

Create the conversation_topics table for tracking active conversation
topics with bounded intent routing, and add topic_id to
conversation_messages for message-level topic association.

Revision ID: 0011_topic_memory
Revises: 0010_ontology_retrieval_profile
Create Date: 2026-07-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_topic_memory"
down_revision: Union[str, None] = "0010_ontology_retrieval_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_topics",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("parent_topic_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("key_entities_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("current_goal", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("active_constraints_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("retracted_goals_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("pending_clarification_json", sa.Text(), nullable=True),
        sa.Column("clarification_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_topics_tenant_id", "conversation_topics", ["tenant_id"])
    op.create_index("ix_conversation_topics_agent_id", "conversation_topics", ["agent_id"])
    op.create_index("ix_conversation_topics_user_id", "conversation_topics", ["user_id"])
    op.create_index("ix_conversation_topics_conversation_id", "conversation_topics", ["conversation_id"])
    op.create_index("ix_conversation_topics_parent_topic_id", "conversation_topics", ["parent_topic_id"])
    op.create_index(
        "uq_conversation_topic_active_scope",
        "conversation_topics",
        ["tenant_id", "agent_id", "user_id", "channel"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("topic_id", sa.Text(), nullable=True),
    )
    op.create_index("ix_conversation_messages_topic_id", "conversation_messages", ["topic_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_topic_id", table_name="conversation_messages")
    op.drop_column("conversation_messages", "topic_id")
    op.drop_index("uq_conversation_topic_active_scope", table_name="conversation_topics")
    op.drop_index("ix_conversation_topics_parent_topic_id", table_name="conversation_topics")
    op.drop_index("ix_conversation_topics_conversation_id", table_name="conversation_topics")
    op.drop_index("ix_conversation_topics_user_id", table_name="conversation_topics")
    op.drop_index("ix_conversation_topics_agent_id", table_name="conversation_topics")
    op.drop_index("ix_conversation_topics_tenant_id", table_name="conversation_topics")
    op.drop_table("conversation_topics")
