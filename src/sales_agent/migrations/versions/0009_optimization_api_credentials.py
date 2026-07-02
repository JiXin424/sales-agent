"""Add optimization API credentials and command audit tables.

Revision ID: 0009_optimization_api_credentials
Revises: 0008_iteration_observability_reports
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_optimization_api_credentials"
down_revision: Union[str, None] = "0008_iteration_observability_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _make_timestamps():
    return [
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # optimization_api_credentials
    op.create_table(
        "optimization_api_credentials",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("token_prefix", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("agent_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("scopes_json", sa.Text(), nullable=False,
                  server_default='["iteration:start","iteration:read","candidate:request","evaluation:rerun"]'),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_oac_tenant_id", "optimization_api_credentials", ["tenant_id"])
    op.create_index("ix_oac_tenant_token_prefix", "optimization_api_credentials", ["tenant_id", "token_prefix"])
    op.create_index("ix_oac_tenant_agent_idx", "optimization_api_credentials", ["tenant_id", "agent_ids_json"])
    op.create_unique_constraint("uq_oac_tenant_subject", "optimization_api_credentials", ["tenant_id", "subject"])

    # optimization_command_audits
    op.create_table(
        "optimization_command_audits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("sanitized_input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="success"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_oca_tenant_id", "optimization_command_audits", ["tenant_id"])
    op.create_index("ix_oca_tenant_credential", "optimization_command_audits", ["tenant_id", "credential_id"])
    op.create_index("ix_oca_tenant_action", "optimization_command_audits", ["tenant_id", "action", "created_at"])
    op.create_unique_constraint(
        "uq_oca_tenant_credential_idempotency",
        "optimization_command_audits",
        ["tenant_id", "credential_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_oca_tenant_action", table_name="optimization_command_audits")
    op.drop_index("ix_oca_tenant_credential", table_name="optimization_command_audits")
    op.drop_index("ix_oca_tenant_id", table_name="optimization_command_audits")
    op.drop_constraint("uq_oca_tenant_credential_idempotency", "optimization_command_audits")
    op.drop_table("optimization_command_audits")

    op.drop_index("ix_oca_tenant_agent_idx", table_name="optimization_api_credentials")
    op.drop_index("ix_oca_tenant_token_prefix", table_name="optimization_api_credentials")
    op.drop_index("ix_oca_tenant_id", table_name="optimization_api_credentials")
    op.drop_constraint("uq_oac_tenant_subject", "optimization_api_credentials")
    op.drop_table("optimization_api_credentials")
