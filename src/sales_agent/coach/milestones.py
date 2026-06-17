"""里程碑定义与幂等 seed。

84 个里程碑：
- 72 维度里程碑 = 6 维度 × 12 阈值。
- 12 全维度里程碑 = 12 阈值，要求六个分数都 ≥ 阈值。

milestone id 采用稳定确定性规则（与业务键对齐），方便幂等 seed 与测试断言：
- 维度：``coach_ms__{dimension}__{threshold}``
- 全维：``coach_ms__all__{threshold}``

解锁逻辑见 progression.py。Negative delta 不会撤销已解锁里程碑。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.constants import (
    DIMENSIONS,
    MILESTONE_THRESHOLDS,
)
from sales_agent.models.coach import CoachMilestone

logger = logging.getLogger(__name__)

SCOPE_DIMENSION = "dimension"
SCOPE_ALL_DIMENSIONS = "all_dimensions"


def dimension_milestone_id(dimension: str, threshold: int) -> str:
    return f"coach_ms__{dimension}__{threshold}"


def all_dimension_milestone_id(threshold: int) -> str:
    return f"coach_ms__all__{threshold}"


def build_all_milestones() -> list[dict[str, Any]]:
    """构造全部 84 条里程碑定义（不写库）。"""
    items: list[dict[str, Any]] = []
    # 维度里程碑
    for dim_key, dim_label in DIMENSIONS:
        for idx, threshold in enumerate(MILESTONE_THRESHOLDS):
            items.append({
                "id": dimension_milestone_id(dim_key, threshold),
                "scope": SCOPE_DIMENSION,
                "dimension": dim_key,
                "threshold": threshold,
                "level_index": idx,
                "name": f"{dim_label}·{threshold}分",
                "description": f"在「{dim_label}」维度达到 {threshold} 分。",
                "badge_key": f"{dim_key}_{threshold}",
            })
    # 全维度里程碑
    for idx, threshold in enumerate(MILESTONE_THRESHOLDS):
        items.append({
            "id": all_dimension_milestone_id(threshold),
            "scope": SCOPE_ALL_DIMENSIONS,
            "dimension": None,
            "threshold": threshold,
            "level_index": idx,
            "name": f"全能·{threshold}分",
            "description": f"六个维度同时达到 {threshold} 分。",
            "badge_key": f"all_{threshold}",
        })
    return items


async def seed_milestones(db: AsyncSession) -> int:
    """幂等 seed：缺失的里程碑才插入，返回新增数量。

    用确定性 id 作主键；已存在则跳过，既不报错也不覆盖。
    """
    defs = build_all_milestones()
    existing_ids = set(
        (
            await db.execute(select(CoachMilestone.id))
        ).scalars().all()
    )
    added = 0
    for d in defs:
        if d["id"] in existing_ids:
            continue
        db.add(CoachMilestone(
            id=d["id"], scope=d["scope"], dimension=d["dimension"],
            threshold=d["threshold"], level_index=d["level_index"],
            name=d["name"], description=d["description"], badge_key=d["badge_key"],
        ))
        added += 1
    if added:
        await db.flush()
        logger.info("Coach milestones seeded: +%d (total defs=%d)", added, len(defs))
    return added
