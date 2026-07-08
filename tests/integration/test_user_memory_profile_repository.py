import json
import pytest
from datetime import datetime, timezone

from sqlalchemy import text

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository


def _scope(user_id="u1"):
    return MemoryScope(tenant_id="t1", agent_id="a1", user_id=user_id)


def _candidate(value="华东区"):
    return MemoryCandidate(
        memory_type="user_fact",
        normalized_key="sales_region",
        content={"key": "sales_region", "value": value},
        evidence_text=f"记住我负责{value}",
        source_kind="explicit_user",
        stability="stable",
        sensitivity="normal",
        confidence_band="confirmed",
    )


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


@pytest.mark.asyncio
async def test_rebuild_profile_persists_projection_and_evidence(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await profile_repo.rebuild_profile_for_scope(scope, now=datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert result.status == "ready"
    profile = await profile_repo.get_current_profile(scope)
    assert profile is not None
    assert profile.profile["work_context"]["sales_region"] == "华东区"
    assert profile.evidence_map["work_context.sales_region"] == result.evidence_map["work_context.sales_region"]


@pytest.mark.asyncio
async def test_rebuild_is_idempotent_for_same_memory_version(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    first = await profile_repo.rebuild_profile_for_scope(scope)
    second = await profile_repo.rebuild_profile_for_scope(scope)

    assert first.version == second.version
    assert first.source_memory_version == second.source_memory_version


@pytest.mark.asyncio
async def test_enqueue_profile_rebuild_is_idempotent(db_session):
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()

    await profile_repo.enqueue_profile_rebuild(scope, reason="memory_activated", source_memory_id="m1")
    await profile_repo.enqueue_profile_rebuild(scope, reason="memory_activated", source_memory_id="m1")

    jobs = await profile_repo.list_pending_rebuild_jobs(limit=10)
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_reconcile_stale_profile_enqueues_missed_rebuild(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")
    await profile_repo.rebuild_profile_for_scope(scope)

    await memory_repo.activate_explicit(scope, _candidate("华南区"), conversation_id="conv2", message_id="msg2")
    enqueued = await profile_repo.enqueue_stale_profile_rebuilds(limit=10)

    assert enqueued == 1
    jobs = await profile_repo.list_pending_rebuild_jobs(limit=10)
    assert any(job.reason == "profile_reconciliation" for job in jobs)
