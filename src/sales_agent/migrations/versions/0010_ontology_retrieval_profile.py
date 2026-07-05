"""Add versioned ontology retrieval parameters.

Revision ID: 0010_ontology_retrieval_profile
Revises: 0009_optimization_api_credentials
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_ontology_retrieval_profile"
down_revision: Union[str, None] = "0009_optimization_api_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("entity_limit", 42),
    ("facts_per_entity", 20),
    ("max_entities_for_prompt", 10),
    ("max_facts_for_prompt", 43),
    ("vector_fallback_top_k", 8),
)


def upgrade() -> None:
    for name, default in _COLUMNS:
        op.add_column(
            "retrieval_profiles",
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default=sa.text(str(default)),
            ),
        )


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("retrieval_profiles", name)
