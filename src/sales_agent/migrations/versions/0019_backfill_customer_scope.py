"""Backfill agent_memories.customer_scope skipped by stamp-head fallback.

Revision ID: 0019_backfill_customer_scope
Revises: 596fe43c3337
Create Date: 2026-07-10

背景
----
0017_pursuit_loop_schema 同时含 ``create_table``（sales_action_cards 新列走
add_column，但表已由 create_all 预建）与老表 ``add_column``
（agent_memories.customer_scope）。init_db 的
``Base.metadata.create_all`` 先于 ``alembic upgrade head`` 跑，create_all 抢先
按最新 model 建好/补好新表，导致从 0015 起跳的库在 ``upgrade`` 撞
DuplicateTableError → ``_run_auto_migrations`` 走 ``stamp head`` 兜底 →
同一 migration 里 agent_memories 老表的 ``add_column`` 被整体跳过。

结果：prod3(taishanyanshi)/test(fuduoduo) 的 ``alembic_version`` 已标到 head，
却缺 ``agent_memories.customer_scope`` 列与 ``ix_agent_memories_customer_scope``
索引（schema 一致性校验报幽灵漂移）。prod2 因原本已在 0018、只单步
升到 596，正常执行了 0017 之外的路径且早已含该列，故本 revision 对其为 no-op。

本 revision 仿 0012_backfill_skipped_columns，用 ``ADD COLUMN IF NOT EXISTS`` /
``CREATE INDEX IF NOT EXISTS`` 幂等补齐：schema 已完整的库（prod2）为 no-op，
缺列库（prod3/test）真正补上。列定义与 0017 原始 DDL 一致
（Text, nullable）。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019_backfill_customer_scope"
down_revision: Union[str, None] = "596fe43c3337"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # ADD/CREATE IF NOT EXISTS 是 PG 专属语法；其它方言由 create_all 兜底，此处跳过。
        return

    # 0017 残留：agent_memories.customer_scope（per-customer facts）
    op.execute(
        "ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS customer_scope TEXT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memories_customer_scope "
        "ON agent_memories (customer_scope)"
    )


def downgrade() -> None:
    # No-op 且刻意为之：customer_scope 本属于 0017，本 revision 仅补救被 stamp-head
    # 兜底跳过的 DDL，并不「拥有」它。downgrade 删列会破坏 schema 已完整的环境（prod2）。
    # 如需回退，请 downgrade 0017 本身。
    pass
