"""Add memory-eval operations tables (sampled traces + promoted regressions).

Revision ID: 0015_memory_eval_operations
Revises: 0014_user_memory_profiles
Create Date: 2026-07-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_memory_eval_operations"
down_revision: Union[str, None] = "0014_user_memory_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- memory_eval_traces: sampled production traces under restricted retention ---
    op.create_table(
        "memory_eval_traces",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("scope_hash", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("retention", sa.Text(), nullable=False, server_default=sa.text("'restricted'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'sampled'")),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_eval_traces_tenant_id", "memory_eval_traces", ["tenant_id"])
    op.create_index(
        "ix_memory_eval_traces_tenant_status", "memory_eval_traces", ["tenant_id", "status"]
    )

    # --- promoted_regressions: anonymized regression scenarios ---
    op.create_table(
        "promoted_regressions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_trace_id", sa.Text(), nullable=False),
        sa.Column("scenario_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("anonymized", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promoted_regressions_tenant_id", "promoted_regressions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_promoted_regressions_tenant_id", table_name="promoted_regressions")
    op.drop_table("promoted_regressions")
    op.drop_index("ix_memory_eval_traces_tenant_status", table_name="memory_eval_traces")
    op.drop_index("ix_memory_eval_traces_tenant_id", table_name="memory_eval_traces")
    op.drop_table("memory_eval_traces")
