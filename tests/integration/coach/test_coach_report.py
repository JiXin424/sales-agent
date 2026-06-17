"""教练报告渲染集成测试 —— scores / iceberg / full。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.report_service import CoachReportService
from sales_agent.models.coach import (
    CoachCompetencyScore,
    CoachDailyEvaluation,
    CoachIcebergAnalysis,
    CoachUserProfile,
)


async def _seed_profile_and_scores(db: AsyncSession, tenant_id: str, agent_id: str, user_id: str):
    db.add(CoachUserProfile(
        tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
        enabled=True, total_points=120, rank="silver", level=1,
        last_evaluated_date="2026-06-14",
    ))
    dims = {
        "customer_identification": 62, "needs_discovery": 48,
        "value_delivery": 71, "trust_building": 55,
        "deal_advancement": 40, "review_reflection": 50,
    }
    deltas = {"customer_identification": 2, "needs_discovery": -1}
    for dim, score in dims.items():
        db.add(CoachCompetencyScore(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            dimension=dim, score=score, last_delta=deltas.get(dim, 0),
        ))


async def _seed_iceberg(db: AsyncSession, tenant_id: str, agent_id: str, user_id: str):
    db.add(CoachIcebergAnalysis(
        tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
        evaluation_id="e1", analysis_date="2026-06-14",
        surface_blocks_json=json.dumps([
            {"type": "value_block", "severity": "high", "description": "价值未量化",
             "evidence_quotes": ["再看看"], "source_conversation_ids": ["c1"]},
            {"type": "needs_block", "severity": "medium", "description": "追问不足",
             "evidence_quotes": [], "source_conversation_ids": []},
        ], ensure_ascii=False),
        deep_blocks_json=json.dumps([
            {"type": "confidence_block", "severity": "medium", "description": "信心不足",
             "evidence_quotes": ["我可能不行"], "source_conversation_ids": ["c1"]},
        ], ensure_ascii=False),
        data_sufficiency="sufficient", summary="价值传递与需求挖掘是主要卡点。",
    ))
    db.add(CoachDailyEvaluation(
        tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
        evaluation_date="2026-06-14", status="success",
        conversation_count=1, user_message_count=3,
        result_json=json.dumps({"next_growth_suggestion": "下次先量化价值再推进。"}, ensure_ascii=False),
        score_deltas_json="{}", iceberg_json="{}", latency_ms=100,
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_scores_report_shows_six_scores(db_session: AsyncSession, sample_tenant: str):
    await _seed_profile_and_scores(db_session, sample_tenant, "a1", "u1")
    report = await CoachReportService(db_session).render_report(
        tenant_id=sample_tenant, agent_id="a1", user_id="u1", report_type="scores",
    )
    assert "客户识别" in report["summary"]
    assert "价值传递" in report["summary"]
    assert "62" in report["summary"]


@pytest.mark.asyncio
async def test_iceberg_report_capped_and_with_evidence(db_session: AsyncSession, sample_tenant: str):
    await _seed_profile_and_scores(db_session, sample_tenant, "a1", "u1")
    await _seed_iceberg(db_session, sample_tenant, "a1", "u1")
    report = await CoachReportService(db_session).render_report(
        tenant_id=sample_tenant, agent_id="a1", user_id="u1", report_type="iceberg",
    )
    titles = [s["title"] for s in report["sections"]]
    assert any("表层" in t for t in titles)
    assert any("深层" in t for t in titles)
    # 内容含证据
    joined = "\n".join(s["content"] for s in report["sections"])
    assert "再看看" in joined


@pytest.mark.asyncio
async def test_full_report_includes_scores_level_iceberg_suggestion(
    db_session: AsyncSession, sample_tenant: str
):
    await _seed_profile_and_scores(db_session, sample_tenant, "a1", "u1")
    await _seed_iceberg(db_session, sample_tenant, "a1", "u1")
    report = await CoachReportService(db_session).render_report(
        tenant_id=sample_tenant, agent_id="a1", user_id="u1", report_type="full",
    )
    titles = [s["title"] for s in report["sections"]]
    assert "六维能力" in titles
    assert "段位与等级" in titles
    assert "下一步建议" in titles
    # 含段位
    joined = "\n".join(s["content"] for s in report["sections"])
    assert "白银" in joined


@pytest.mark.asyncio
async def test_no_data_report_is_graceful(db_session: AsyncSession, sample_tenant: str):
    report = await CoachReportService(db_session).render_report(
        tenant_id=sample_tenant, agent_id="a1", user_id="nobody", report_type="scores",
    )
    assert "不足" in report["summary"] or "数据" in report["summary"]


@pytest.mark.asyncio
async def test_agent_scoping_in_report(db_session: AsyncSession, sample_tenant: str):
    await _seed_profile_and_scores(db_session, sample_tenant, "a1", "u1")
    # agent b 查不到 a1 的数据
    report = await CoachReportService(db_session).render_report(
        tenant_id=sample_tenant, agent_id="a2", user_id="u1", report_type="scores",
    )
    assert "不足" in report["summary"] or "数据" in report["summary"]
