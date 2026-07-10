import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from sales_agent.models.atomic_memory import AtomicMemory, MemoryOutboxJob, MemoryAuditEvent
from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository


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


def _scope(user_id="u1", tenant_id="t1", agent_id="a1"):
    return MemoryScope(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)


def _candidate(value="华东区", source_kind="explicit_user"):
    return MemoryCandidate(
        memory_type="user_fact",
        normalized_key="sales_region",
        content={"key": "sales_region", "value": value},
        evidence_text=f"我负责{value}",
        source_kind=source_kind,
        stability="stable",
        sensitivity="normal",
        confidence_band="confirmed" if source_kind == "explicit_user" else "candidate",
    )


@pytest.mark.asyncio
async def test_activate_explicit_and_list_active_scope(db_session):
    repo = AtomicMemoryRepository(db_session)
    result = await repo.activate_explicit(
        _scope(),
        _candidate(),
        conversation_id="conv1",
        message_id="msg1",
        now=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert result.status == "success"
    rows = await repo.list_active_memories(_scope())
    assert len(rows) == 1
    assert rows[0].normalized_key == "sales_region"
    assert rows[0].content["value"] == "华东区"
    assert await repo.list_active_memories(_scope(user_id="u2")) == []


@pytest.mark.asyncio
async def test_correction_supersedes_old_memory(db_session):
    repo = AtomicMemoryRepository(db_session)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await repo.correct_memory(
        _scope(),
        normalized_key="sales_region",
        new_candidate=_candidate("华南区"),
        conversation_id="conv2",
        message_id="msg2",
    )

    assert result.status == "success"
    active = await repo.list_active_memories(_scope())
    assert len(active) == 1
    assert active[0].content["value"] == "华南区"
    assert active[0].supersedes_id is not None


@pytest.mark.asyncio
async def test_forget_deletes_exact_single_match_immediately(db_session):
    repo = AtomicMemoryRepository(db_session)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await repo.forget_memory(_scope(), normalized_key="sales_region", confirm_broad=False)

    assert result.status == "success"
    assert await repo.list_active_memories(_scope()) == []


@pytest.mark.asyncio
async def test_get_memory_with_provenance_is_scope_guarded(db_session):
    repo = AtomicMemoryRepository(db_session)
    result = await repo.activate_explicit(
        _scope(),
        _candidate("华东区"),
        conversation_id="conv1",
        message_id="msg1",
    )

    record = await repo.get_memory_with_provenance(_scope(), result.memory_ids[0])
    assert record is not None
    assert record.source_conversation_id == "conv1"
    assert record.source_message_ids == ["msg1"]
    assert await repo.get_memory_with_provenance(_scope(user_id="u2"), result.memory_ids[0]) is None


@pytest.mark.asyncio
async def test_lazy_expiry_excludes_stale_active_memory(db_session):
    repo = AtomicMemoryRepository(db_session)
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1", now=now)

    rows = await repo.expire_due_memories(now + timedelta(days=181))

    assert rows.expired_count == 1
    assert await repo.list_active_memories(_scope(), now=now + timedelta(days=181)) == []


@pytest.mark.asyncio
async def test_corroborated_second_user_evidence_activates_candidate(db_session):
    repo = AtomicMemoryRepository(db_session)
    first = await repo.corroborate_candidate(
        _scope(),
        _candidate("华东区", source_kind="inferred_user"),
        conversation_id="conv1",
        message_id="msg1",
    )
    assert first.status == "success"
    assert first.reason_code == "stored_candidate"
    assert await repo.list_active_memories(_scope()) == []

    second = await repo.corroborate_candidate(
        _scope(),
        _candidate("华东区", source_kind="inferred_user"),
        conversation_id="conv2",
        message_id="msg2",
    )

    assert second.status == "success"
    assert second.reason_code == "corroborated_two_evidence"
    active = await repo.list_active_memories(_scope())
    assert len(active) == 1
    assert active[0].evidence_count == 2
    assert set(active[0].source_message_ids) == {"msg1", "msg2"}
