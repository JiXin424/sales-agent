import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_atomic_memory_schema_has_scope_and_unique_indexes(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE tablename IN ('agent_memories', 'memory_outbox')
                """
            )
        )
    ).mappings().all()

    index_defs = {row["indexname"]: row["indexdef"] for row in rows}
    assert "ix_agent_memories_scope_status" in index_defs
    assert "ix_agent_memories_source_message" in index_defs
    assert "uq_agent_memory_active_single_value" in index_defs
    assert "uq_memory_outbox_event_operation" in index_defs
