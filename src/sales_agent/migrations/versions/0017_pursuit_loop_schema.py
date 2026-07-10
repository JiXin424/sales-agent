"""pursuit_loop_schema

Revision ID: 0017
Revises: 0016_sales_action_cards
Create Date: 2026-07-10 15:40:04.321541

Plan/Observe/Replan columns on sales_action_cards + customer_scope on agent_memories.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, None] = "0016_sales_action_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SalesActionCard — all nullable, backward-compat
    for col, col_type in [
        ("success_criteria", sa.Text()),
        ("pursuit_goal", sa.Text()),
        ("outcome_tag", sa.Text()),
        ("outcome_note", sa.Text()),
        ("outcome_met_signal", sa.Boolean()),
        ("outcome_captured_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("sales_action_cards", sa.Column(col, col_type, nullable=True))

    # AtomicMemory (table: agent_memories) — per-customer facts
    op.add_column("agent_memories", sa.Column("customer_scope", sa.Text(), nullable=True))
    op.create_index("ix_agent_memories_customer_scope", "agent_memories", ["customer_scope"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_customer_scope", table_name="agent_memories")
    op.drop_column("agent_memories", "customer_scope")
    for col in [
        "outcome_captured_at", "outcome_met_signal", "outcome_note",
        "outcome_tag", "pursuit_goal", "success_criteria",
    ]:
        op.drop_column("sales_action_cards", col)
