"""反馈根因分类服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.feedback import Feedback

logger = logging.getLogger(__name__)

# 合法的反馈分类
FEEDBACK_CATEGORIES = [
    "wrong_answer",
    "missing_knowledge",
    "bad_retrieval",
    "bad_prompt",
    "wrong_task_route",
    "unsafe_answer",
    "too_generic",
    "too_slow",
    "format_problem",
]


class FeedbackClassificationService:
    """反馈根因分类。

    用法::

        svc = FeedbackClassificationService(db)
        fb = await svc.classify(tenant_id, feedback_id, ["wrong_answer", "bad_prompt"])
        summary = await svc.get_categories_summary(tenant_id)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def classify(
        self, tenant_id: str, feedback_id: str, categories: list[str]
    ) -> Feedback:
        """为反馈分配根因分类。

        校验分类合法性后更新 categories_json。
        """
        # 校验分类
        invalid = set(categories) - set(FEEDBACK_CATEGORIES)
        if invalid:
            raise ValueError(
                f"Invalid categories: {invalid}. Must be one of {FEEDBACK_CATEGORIES}"
            )

        fb_stmt = select(Feedback).where(
            Feedback.id == feedback_id,
            Feedback.tenant_id == tenant_id,
        )
        fb = (await self.db.execute(fb_stmt)).scalar_one_or_none()
        if fb is None:
            raise ValueError(f"Feedback not found: {feedback_id!r} for tenant {tenant_id!r}")

        fb.categories_json = json.dumps(categories, ensure_ascii=False)
        await self.db.flush()
        return fb

    async def get_categories_summary(self, tenant_id: str) -> dict[str, int]:
        """获取按分类聚合的反馈计数。"""
        stmt = select(Feedback.categories_json).where(
            Feedback.tenant_id == tenant_id,
            Feedback.categories_json != "[]",
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        counts: dict[str, int] = {}
        for cats_json in rows:
            try:
                cats = json.loads(cats_json)
                for cat in cats:
                    counts[cat] = counts.get(cat, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        return counts

    async def list_by_category(
        self,
        tenant_id: str,
        category: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Feedback], int]:
        """按分类列出反馈。

        由于 categories_json 是 JSON 数组字符串，使用 LIKE 做模糊匹配。
        """
        if category not in FEEDBACK_CATEGORIES:
            raise ValueError(f"Invalid category: {category!r}")

        conditions = [
            Feedback.tenant_id == tenant_id,
            Feedback.categories_json.contains(f'"{category}"'),
        ]

        count_stmt = select(func.count()).select_from(Feedback).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Feedback)
            .where(*conditions)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total
