"""baseline: 现有数据库结构

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-22

对已有生产/开发库用 ``alembic stamp head`` 标记此 revision，不执行任何 DDL
（表已由 ``Base.metadata.create_all`` 创建）。新库可直接 ``alembic upgrade head``，
此时本 revision 仍是空操作，实际建表由 create_all 在应用启动时完成；后续 schema
变更走 alembic 增量 migration。
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline: 不执行 DDL，仅作为版本链起点。
    pass


def downgrade() -> None:
    pass
