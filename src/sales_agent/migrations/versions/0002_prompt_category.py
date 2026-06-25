"""add prompt_category / prompt_key / required_placeholders_json to prompt_versions

Revision ID: 0002_prompt_category
Revises: 0001_baseline
Create Date: 2026-06-22

把 PromptVersion 表从「只能存 12 类业务 task prompt」扩展为「统一承载所有层
prompt（task/system/router/risk/coach）」。新增 prompt_category + prompt_key 双维度
定位，task_type 改为 nullable（非 task 类无 task_type），并回填历史 task 行的 prompt_key。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_prompt_category"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prompt_versions",
        sa.Column("prompt_category", sa.Text(), nullable=False, server_default="task"),
    )
    op.add_column(
        "prompt_versions",
        sa.Column("prompt_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "prompt_versions",
        sa.Column(
            "required_placeholders_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    # task_type 允许 NULL：非 task 类 prompt（system/router/risk/coach）没有 task_type
    op.alter_column(
        "prompt_versions",
        "task_type",
        existing_type=sa.Text(),
        nullable=True,
    )
    # 回填：历史 task 行 prompt_key = task_type
    op.execute(
        "UPDATE prompt_versions SET prompt_key = task_type WHERE prompt_key IS NULL"
    )
    op.create_index(
        "ix_prompt_versions_tenant_cat_key_status",
        "prompt_versions",
        ["tenant_id", "prompt_category", "prompt_key", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prompt_versions_tenant_cat_key_status",
        table_name="prompt_versions",
    )
    # 回填 NULL task_type 再恢复 NOT NULL
    op.execute(
        "UPDATE prompt_versions SET task_type = 'general_sales_coaching' "
        "WHERE task_type IS NULL"
    )
    op.alter_column(
        "prompt_versions",
        "task_type",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_column("prompt_versions", "required_placeholders_json")
    op.drop_column("prompt_versions", "prompt_key")
    op.drop_column("prompt_versions", "prompt_category")
