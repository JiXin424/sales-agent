"""Integration tests for memory-eval operations ORM models (Spec 4 §8, §9).

Verifies that ``MemoryEvalTraceRecord`` (sampled production traces under
restricted retention) and ``PromotedRegression`` (anonymized regression
scenarios) round-trip through the database via ``Base.metadata.create_all``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from sales_agent.models.memory_eval import MemoryEvalTraceRecord, PromotedRegression


@pytest.mark.asyncio
async def test_trace_record_roundtrip(db_session):
    rec = MemoryEvalTraceRecord(
        tenant_id="t1",
        scope_hash="h:abc",
        thread_id="online:t:a:c:u",
        trace_json={"topic_id": "topic-7"},
        retention="restricted",
        status="sampled",
    )
    db_session.add(rec)
    await db_session.flush()
    rows = (
        (
            await db_session.execute(
                select(MemoryEvalTraceRecord).where(
                    MemoryEvalTraceRecord.scope_hash == "h:abc"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].trace_json["topic_id"] == "topic-7"
    assert rows[0].retention == "restricted"
    assert rows[0].status == "sampled"


@pytest.mark.asyncio
async def test_promoted_regression_roundtrip(db_session):
    pr = PromotedRegression(
        tenant_id="t1",
        source_trace_id="tr-1",
        scenario_json={"id": "promoted-001"},
        status="draft",
        anonymized=True,
    )
    db_session.add(pr)
    await db_session.flush()
    rows = (await db_session.execute(select(PromotedRegression))).scalars().all()
    assert len(rows) == 1
    assert rows[0].anonymized is True
    assert rows[0].scenario_json["id"] == "promoted-001"
    assert rows[0].status == "draft"
