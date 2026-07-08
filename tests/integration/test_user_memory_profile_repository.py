import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_user_profile_schema_has_scope_and_rebuild_indexes(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE tablename IN ('user_memory_profiles', 'user_profile_rebuild_jobs')
                """
            )
        )
    ).mappings().all()

    indexes = {row["indexname"]: row["indexdef"] for row in rows}
    assert "uq_user_memory_profile_current_scope" in indexes
    assert "ix_user_memory_profiles_scope" in indexes
    assert "ix_user_profile_rebuild_jobs_poll" in indexes
    assert "uq_user_profile_rebuild_scope_reason" in indexes
