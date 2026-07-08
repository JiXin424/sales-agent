"""Add user memory profile projection tables.

Revision ID: 0014_user_memory_profiles
Revises: 0013_governed_long_term_memory
Create Date: 2026-07-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_user_memory_profiles"
down_revision: Union[str, None] = "0013_governed_long_term_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_memory_profiles",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("evidence_map_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_memory_version", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_memory_profiles_tenant_id", "user_memory_profiles", ["tenant_id"])
    op.create_index("ix_user_memory_profiles_agent_id", "user_memory_profiles", ["agent_id"])
    op.create_index("ix_user_memory_profiles_user_id", "user_memory_profiles", ["user_id"])
    op.create_index("ix_user_memory_profiles_scope", "user_memory_profiles", ["tenant_id", "agent_id", "user_id"])
    op.create_index(
        "uq_user_memory_profile_current_scope",
        "user_memory_profiles",
        ["tenant_id", "agent_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ready', 'rebuilding', 'degraded')"),
    )

    op.create_table(
        "user_profile_rebuild_jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_memory_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "agent_id", "user_id", "reason", "source_memory_id",
            name="uq_user_profile_rebuild_scope_reason",
        ),
    )
    op.create_index("ix_user_profile_rebuild_jobs_tenant_id", "user_profile_rebuild_jobs", ["tenant_id"])
    op.create_index("ix_user_profile_rebuild_jobs_agent_id", "user_profile_rebuild_jobs", ["agent_id"])
    op.create_index("ix_user_profile_rebuild_jobs_user_id", "user_profile_rebuild_jobs", ["user_id"])
    op.create_index("ix_user_profile_rebuild_jobs_source_memory_id", "user_profile_rebuild_jobs", ["source_memory_id"])
    op.create_index("ix_user_profile_rebuild_jobs_poll", "user_profile_rebuild_jobs", ["status", "available_at"])
    op.create_index("ix_user_profile_rebuild_jobs_scope", "user_profile_rebuild_jobs", ["tenant_id", "agent_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_profile_rebuild_jobs_scope", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_poll", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_source_memory_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_user_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_agent_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_tenant_id", table_name="user_profile_rebuild_jobs")
    op.drop_table("user_profile_rebuild_jobs")
    op.drop_index("uq_user_memory_profile_current_scope", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_scope", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_user_id", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_agent_id", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_tenant_id", table_name="user_memory_profiles")
    op.drop_table("user_memory_profiles")
