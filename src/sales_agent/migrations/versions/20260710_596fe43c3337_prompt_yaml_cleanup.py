"""prompt_yaml_cleanup

Revision ID: 596fe43c3337
Revises: 0018_card_callback_events
Create Date: 2026-07-10 17:25:34.728699

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '596fe43c3337'
down_revision: Union[str, None] = '0018_card_callback_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('agent_prompt_sets')
    op.drop_table('prompt_versions')
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('prompt_set_id')
    with op.batch_alter_table('optimization_releases') as batch_op:
        batch_op.drop_column('prompt_set_id')


def downgrade() -> None:
    pass  # 不可逆：YAML 是新的唯一 prompt 来源，DB 数据已丢弃
