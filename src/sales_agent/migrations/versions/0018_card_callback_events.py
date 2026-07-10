"""Add dingtalk_card_callback_events table (card 点赞/点踩/问题反馈 raw capture).

Revision ID: 0018_card_callback_events
Revises: 0017
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_card_callback_events"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 钉钉互动卡片按钮回调原始事件（capture-first：原样落库，M2 再映射到 feedbacks）
    op.create_table(
        "dingtalk_card_callback_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("dingtalk_user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("card_instance_id", sa.Text(), nullable=True),
        sa.Column("corp_id", sa.Text(), nullable=True),
        sa.Column("space_id", sa.Text(), nullable=True),
        sa.Column("content_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("extension_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dt_card_cb_tenant_created",
        "dingtalk_card_callback_events",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_dt_card_cb_card_instance",
        "dingtalk_card_callback_events",
        ["card_instance_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dt_card_cb_card_instance", table_name="dingtalk_card_callback_events")
    op.drop_index("ix_dt_card_cb_tenant_created", table_name="dingtalk_card_callback_events")
    op.drop_table("dingtalk_card_callback_events")
