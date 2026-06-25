"""add ontology metadata to ingestion_jobs

Revision ID: 0003_ontology_neo4j_metadata
Revises: 0002_prompt_category
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_ontology_neo4j_metadata"
down_revision: Union[str, None] = "0002_prompt_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("agent_id", sa.Text(), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("engine", sa.Text(), nullable=False, server_default="legacy_rag"))
    op.add_column("ingestion_jobs", sa.Column("stage", sa.Text(), nullable=False, server_default="queued"))
    op.add_column("ingestion_jobs", sa.Column("entities_created", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("entities_merged", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_created", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_active", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_pending_review", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_rejected", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("conflicts_created", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_ingestion_jobs_agent_id", "ingestion_jobs", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_agent_id", table_name="ingestion_jobs")
    for column in (
        "conflicts_created",
        "facts_rejected",
        "facts_pending_review",
        "facts_active",
        "facts_created",
        "entities_merged",
        "entities_created",
        "stage",
        "engine",
        "agent_id",
    ):
        op.drop_column("ingestion_jobs", column)
