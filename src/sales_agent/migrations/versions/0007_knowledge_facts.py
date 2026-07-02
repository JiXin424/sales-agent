"""Add knowledge_facts table.

Revision ID: 0007_knowledge_facts
Revises: 0006_optimization_workflow
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_knowledge_facts"
down_revision: Union[str, None] = "0006_optimization_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_facts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("knowledge_version_id", sa.Text(), nullable=False),
        sa.Column("document_revision_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("qualifiers_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_offsets_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("effective_start", sa.Text(), nullable=True),
        sa.Column("effective_end", sa.Text(), nullable=True),
        sa.Column("extractor_name", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column("canonical_hash", sa.Text(), nullable=False),
        sa.Column("conflict_status", sa.Text(), nullable=False, server_default="unique"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kf_tenant_id", "knowledge_facts", ["tenant_id"])
    op.create_index("ix_kf_knowledge_version_id", "knowledge_facts", ["knowledge_version_id"])
    op.create_index("ix_kf_tenant_version", "knowledge_facts", ["tenant_id", "knowledge_version_id"])
    op.create_index("ix_kf_tenant_doc_rev", "knowledge_facts", ["tenant_id", "document_revision_id"])
    op.create_index("ix_kf_canonical_hash", "knowledge_facts", ["canonical_hash"])
    op.create_unique_constraint("uq_kf_tenant_hash", "knowledge_facts", ["tenant_id", "canonical_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_kf_tenant_hash", "knowledge_facts")
    op.drop_index("ix_kf_canonical_hash", table_name="knowledge_facts")
    op.drop_index("ix_kf_tenant_doc_rev", table_name="knowledge_facts")
    op.drop_index("ix_kf_tenant_version", table_name="knowledge_facts")
    op.drop_index("ix_kf_knowledge_version_id", table_name="knowledge_facts")
    op.drop_index("ix_kf_tenant_id", table_name="knowledge_facts")
    op.drop_table("knowledge_facts")
