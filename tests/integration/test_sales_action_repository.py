"""销售动作卡片与提醒仓储的集成测试。"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_sales_action_tables_and_indexes_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE tablename IN (
                   'sales_action_cards',
                   'sales_action_reminders',
                   'sales_action_deliveries',
                   'sales_action_events'
                 )
                """
            )
        )
    ).scalars().all()
    indexes = set(rows)
    assert "ix_sales_action_cards_scope_status" in indexes
    assert "ix_sales_action_cards_scheduled" in indexes
    assert "uq_sales_action_reminders_idempotency" in indexes
    assert "ix_sales_action_reminders_due" in indexes
    assert "ix_sales_action_deliveries_scope" in indexes
    assert "ix_sales_action_events_action" in indexes
