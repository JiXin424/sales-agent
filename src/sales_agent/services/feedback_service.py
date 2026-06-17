"""反馈服务：独立反馈表的持久化和查询。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id
from sales_agent.models.conversation import Conversation
from sales_agent.models.feedback import Feedback

logger = logging.getLogger(__name__)


class FeedbackService:
    """反馈持久化服务。

    用法::

        svc = FeedbackService(db)
        fb = await svc.submit(tenant_id, conv_id, user_id, "up", "很有帮助")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit(
        self,
        tenant_id: str,
        conversation_id: str,
        user_id: str,
        rating: str,
        feedback_text: str = "",
        labels: list[str] | None = None,
    ) -> Feedback:
        """提交反馈。

        校验 conversation 存在且属于该 tenant。
        rating 必须是 "up" 或 "down"。
        """
        if rating not in ("up", "down"):
            raise ValueError(f"Invalid rating: {rating!r}. Must be 'up' or 'down'.")

        # 校验会话存在且属于租户
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ValueError(
                f"Conversation not found: {conversation_id!r} for tenant {tenant_id!r}"
            )

        fb = Feedback(
            id=generate_id(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            rating=rating,
            feedback_text=feedback_text,
            labels_json=json.dumps(labels or [], ensure_ascii=False),
        )
        self.db.add(fb)
        await self.db.flush()
        return fb

    async def get_by_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[Feedback]:
        """获取某个会话的反馈列表。"""
        stmt = (
            select(Feedback)
            .where(
                Feedback.tenant_id == tenant_id,
                Feedback.conversation_id == conversation_id,
            )
            .order_by(Feedback.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self, tenant_id: str, feedback_id: str
    ) -> Feedback | None:
        """获取单条反馈（含租户隔离检查）。"""
        stmt = select(Feedback).where(
            Feedback.id == feedback_id,
            Feedback.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_review_status(
        self, tenant_id: str, feedback_id: str, review_status: str
    ) -> Feedback:
        """更新反馈的处理状态。"""
        if review_status not in ("open", "reviewed", "ignored"):
            raise ValueError(f"Invalid review_status: {review_status!r}")
        fb = await self.get_by_id(tenant_id, feedback_id)
        if fb is None:
            raise ValueError(f"Feedback not found: {feedback_id!r}")
        fb.review_status = review_status
        await self.db.flush()
        return fb

    async def list_by_tenant(
        self,
        tenant_id: str,
        rating: str | None = None,
        review_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Feedback], int]:
        """列出租户的反馈，支持按 rating 和 review_status 过滤。返回 (items, total)。"""
        conditions = [Feedback.tenant_id == tenant_id]
        if rating is not None:
            conditions.append(Feedback.rating == rating)
        if review_status is not None:
            conditions.append(Feedback.review_status == review_status)

        # Count
        count_stmt = select(func.count()).select_from(Feedback).where(*conditions)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data
        stmt = (
            select(Feedback)
            .where(*conditions)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_summary(self, tenant_id: str) -> dict[str, Any]:
        """获取租户反馈汇总（按 rating 计数）。"""
        stmt = (
            select(Feedback.rating, func.count())
            .where(Feedback.tenant_id == tenant_id)
            .group_by(Feedback.rating)
        )
        result = await self.db.execute(stmt)
        counts = dict(result.all())

        return {
            "up": counts.get("up", 0),
            "down": counts.get("down", 0),
            "total": sum(counts.values()),
        }
