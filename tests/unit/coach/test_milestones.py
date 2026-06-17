"""里程碑定义与 seed 的单元测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.constants import DIMENSIONS, MILESTONE_THRESHOLDS
from sales_agent.coach.milestones import (
    all_dimension_milestone_id,
    build_all_milestones,
    dimension_milestone_id,
    seed_milestones,
)
from sales_agent.models.coach import CoachMilestone


def test_build_all_milestones_counts_and_ids():
    items = build_all_milestones()
    assert len(items) == 84
    dim_items = [i for i in items if i["scope"] == "dimension"]
    all_items = [i for i in items if i["scope"] == "all_dimensions"]
    assert len(dim_items) == 6 * 12 == 72
    assert len(all_items) == 12

    # 确定性 id
    ids = {i["id"] for i in items}
    assert len(ids) == 84
    assert dimension_milestone_id("needs_discovery", 50) in ids
    assert all_dimension_milestone_id(80) in ids


def test_deterministic_ids_stable():
    assert dimension_milestone_id("trust_building", 40) == "coach_ms__trust_building__40"
    assert all_dimension_milestone_id(100) == "coach_ms__all__100"


def test_thresholds_cover_expected_set():
    items = build_all_milestones()
    # 每个维度都有全部 12 阈值
    for dim_key, _ in DIMENSIONS:
        ths = {i["threshold"] for i in items if i["dimension"] == dim_key}
        assert ths == set(MILESTONE_THRESHOLDS)


@pytest.mark.asyncio
async def test_seed_milestones_idempotent(db_session: AsyncSession):
    # 共享物理 DB 下，init_db 可能已被其它测试/应用启动 seed 过 84 条；
    # seed 必须幂等：无论初始状态，seed 后总数恒为 84，再次 seed 新增 0。
    await seed_milestones(db_session)
    total = (await db_session.execute(select(CoachMilestone))).scalars().all()
    assert len(total) == 84
    # 再次 seed：0 新增，总数仍为 84
    added2 = await seed_milestones(db_session)
    assert added2 == 0
    total2 = (await db_session.execute(select(CoachMilestone))).scalars().all()
    assert len(total2) == 84
