"""质量审查队列服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_run import AgentRun
from sales_agent.models.base import generate_id
from sales_agent.models.conversation import Conversation, RetrievalLog
from sales_agent.models.feedback import Feedback
from sales_agent.models.review_item import ReviewItem

logger = logging.getLogger(__name__)

# 合法的原因标签
VALID_REASONS = {
    "negative_feedback",
    "high_risk",
    "low_score",
    "model_error",
    "retrieval_miss",
    "manual_flag",
}

# 合法的优先级
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

# 合法的状态
VALID_STATUSES = {"open", "in_progress", "resolved", "ignored"}


class ReviewQueueService:
    """质量审查队列管理。

    用法::

        svc = ReviewQueueService(db)
        count = await svc.scan_and_enqueue(tenant_id)
        items, total = await svc.list_items(tenant_id, status="open")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def scan_and_enqueue(self, tenant_id: str) -> int:
        """扫描最近的会话/反馈/运行，为满足条件的创建审查条目。

        返回新创建的条目数。
        """
        created = 0

        # 1. 负反馈
        neg_fb_stmt = select(Feedback).where(
            Feedback.tenant_id == tenant_id,
            Feedback.rating == "down",
        )
        neg_fb_result = await self.db.execute(neg_fb_stmt)
        for fb in neg_fb_result.scalars().all():
            if not await self._exists(tenant_id, fb.conversation_id, "negative_feedback"):
                await self._create(
                    tenant_id=tenant_id,
                    conversation_id=fb.conversation_id,
                    feedback_id=fb.id,
                    reason="negative_feedback",
                    priority="high",
                )
                created += 1

        # 2. 高风险会话
        risk_stmt = select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.risk_json.isnot(None),
            Conversation.risk_json != "",
            Conversation.risk_json != "{}",
        )
        risk_result = await self.db.execute(risk_stmt)
        for conv in risk_result.scalars().all():
            try:
                risk_data = json.loads(conv.risk_json)
                if risk_data.get("level") in ("high", "critical"):
                    if not await self._exists(tenant_id, conv.id, "high_risk"):
                        await self._create(
                            tenant_id=tenant_id,
                            conversation_id=conv.id,
                            reason="high_risk",
                            priority="critical",
                        )
                        created += 1
            except (json.JSONDecodeError, TypeError):
                pass

        # 3. 模型错误
        error_runs_stmt = select(AgentRun).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.status == "failed",
        )
        error_runs_result = await self.db.execute(error_runs_stmt)
        for run in error_runs_result.scalars().all():
            if not await self._exists(tenant_id, run.conversation_id, "model_error"):
                await self._create(
                    tenant_id=tenant_id,
                    conversation_id=run.conversation_id,
                    agent_run_id=run.id,
                    reason="model_error",
                    priority="high",
                )
                created += 1

        # 4. 检索缺失：knowledge_qa 任务但没有检索到结果
        miss_stmt = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.task_type == "knowledge_qa",
            )
        )
        miss_result = await self.db.execute(miss_stmt)
        for conv in miss_result.scalars().all():
            sources = []
            try:
                sources = json.loads(conv.sources_json) if conv.sources_json else []
            except (json.JSONDecodeError, TypeError):
                pass
            if not sources:
                if not await self._exists(tenant_id, conv.id, "retrieval_miss"):
                    await self._create(
                        tenant_id=tenant_id,
                        conversation_id=conv.id,
                        reason="retrieval_miss",
                        priority="medium",
                    )
                    created += 1

        await self.db.flush()
        return created

    async def create_manual(
        self,
        tenant_id: str,
        conversation_id: str,
        reason: str = "manual_flag",
        priority: str = "medium",
        notes: dict[str, Any] | None = None,
        assignee: str | None = None,
    ) -> ReviewItem:
        """手动创建审查条目。"""
        if reason not in VALID_REASONS:
            raise ValueError(f"Invalid reason: {reason!r}. Must be one of {VALID_REASONS}")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {priority!r}")

        item = ReviewItem(
            id=generate_id(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
            status="open",
            assignee=assignee,
            notes_json=json.dumps(notes or {}, ensure_ascii=False),
        )
        self.db.add(item)
        await self.db.flush()
        return item

    async def list_items(
        self,
        tenant_id: str,
        status: str | None = None,
        reason: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReviewItem], int]:
        """列出审查条目。返回 (items, total)。"""
        conditions = [ReviewItem.tenant_id == tenant_id]
        if status:
            conditions.append(ReviewItem.status == status)
        if reason:
            conditions.append(ReviewItem.reason == reason)
        if priority:
            conditions.append(ReviewItem.priority == priority)
        if assignee:
            conditions.append(ReviewItem.assignee == assignee)

        count_stmt = select(func.count()).select_from(ReviewItem).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(ReviewItem)
            .where(*conditions)
            .order_by(ReviewItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def get_item(self, tenant_id: str, item_id: str) -> ReviewItem | None:
        """获取单条审查条目。"""
        stmt = select(ReviewItem).where(
            ReviewItem.id == item_id,
            ReviewItem.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def update_status(
        self,
        tenant_id: str,
        item_id: str,
        status: str,
        assignee: str | None = None,
        notes: dict[str, Any] | None = None,
    ) -> ReviewItem:
        """更新审查条目状态。"""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}. Must be one of {VALID_STATUSES}")

        item = await self.get_item(tenant_id, item_id)
        if item is None:
            raise ValueError(f"Review item not found: {item_id!r}")

        item.status = status
        if assignee is not None:
            item.assignee = assignee
        if notes is not None:
            existing = json.loads(item.notes_json) if item.notes_json else {}
            existing.update(notes)
            item.notes_json = json.dumps(existing, ensure_ascii=False)

        await self.db.flush()
        return item

    async def _exists(
        self, tenant_id: str, conversation_id: str, reason: str
    ) -> bool:
        """检查是否已存在相同 conversation+reason 的条目（去重）。"""
        stmt = select(func.count()).select_from(ReviewItem).where(
            ReviewItem.tenant_id == tenant_id,
            ReviewItem.conversation_id == conversation_id,
            ReviewItem.reason == reason,
        )
        count = (await self.db.execute(stmt)).scalar() or 0
        return count > 0

    async def _create(
        self,
        tenant_id: str,
        conversation_id: str,
        reason: str,
        priority: str = "medium",
        feedback_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> ReviewItem:
        """创建审查条目（内部方法）。"""
        item = ReviewItem(
            id=generate_id(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            feedback_id=feedback_id,
            agent_run_id=agent_run_id,
            reason=reason,
            priority=priority,
            status="open",
        )
        self.db.add(item)
        return item
