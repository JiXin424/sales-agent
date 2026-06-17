"""知识缺口追踪服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id
from sales_agent.models.knowledge_gap import KnowledgeGap

logger = logging.getLogger(__name__)

# 合法的状态
VALID_STATUSES = {"open", "document_needed", "uploaded", "verified", "ignored"}

# 合法的状态转换
VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"document_needed", "ignored"},
    "document_needed": {"uploaded", "ignored"},
    "uploaded": {"verified", "ignored"},
    "verified": set(),  # 终态
    "ignored": set(),   # 终态
}


class KnowledgeGapService:
    """知识缺口生命周期管理。

    用法::

        svc = KnowledgeGapService(db)
        gap = await svc.create(tenant_id, "产品定价策略", source_conversation_id=conv_id)
        gap = await svc.transition(tenant_id, gap.id, "document_needed")
        gap = await svc.link_document(tenant_id, gap.id, doc_id)
        gap = await svc.transition(tenant_id, gap.id, "verified")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        tenant_id: str,
        title: str,
        description: str | None = None,
        source_conversation_id: str | None = None,
        source_feedback_id: str | None = None,
        priority: str = "medium",
        keywords: list[str] | None = None,
    ) -> KnowledgeGap:
        """创建知识缺口条目。"""
        gap = KnowledgeGap(
            id=generate_id(),
            tenant_id=tenant_id,
            title=title,
            description=description,
            source_conversation_id=source_conversation_id,
            source_feedback_id=source_feedback_id,
            status="open",
            priority=priority,
            keywords_json=json.dumps(keywords or [], ensure_ascii=False),
        )
        self.db.add(gap)
        await self.db.flush()
        return gap

    async def transition(
        self,
        tenant_id: str,
        gap_id: str,
        new_status: str,
    ) -> KnowledgeGap:
        """转换知识缺口状态。"""
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {new_status!r}. Must be one of {VALID_STATUSES}")

        gap = await self.get_gap(tenant_id, gap_id)
        if gap is None:
            raise ValueError(f"Knowledge gap not found: {gap_id!r}")

        allowed = VALID_TRANSITIONS.get(gap.status, set())
        if new_status not in allowed and new_status != gap.status:
            raise ValueError(
                f"Invalid transition: {gap.status!r} -> {new_status!r}. "
                f"Allowed transitions from {gap.status!r}: {allowed}"
            )

        gap.status = new_status
        await self.db.flush()
        return gap

    async def link_document(
        self,
        tenant_id: str,
        gap_id: str,
        document_id: str,
    ) -> KnowledgeGap:
        """关联文档到知识缺口。"""
        gap = await self.get_gap(tenant_id, gap_id)
        if gap is None:
            raise ValueError(f"Knowledge gap not found: {gap_id!r}")

        gap.linked_document_id = document_id
        await self.db.flush()
        return gap

    async def list_gaps(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[KnowledgeGap], int]:
        """列出知识缺口。返回 (items, total)。"""
        conditions = [KnowledgeGap.tenant_id == tenant_id]
        if status:
            conditions.append(KnowledgeGap.status == status)

        count_stmt = select(func.count()).select_from(KnowledgeGap).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(KnowledgeGap)
            .where(*conditions)
            .order_by(KnowledgeGap.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def get_gap(self, tenant_id: str, gap_id: str) -> KnowledgeGap | None:
        """获取单条知识缺口。"""
        stmt = select(KnowledgeGap).where(
            KnowledgeGap.id == gap_id,
            KnowledgeGap.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_summary(self, tenant_id: str) -> dict[str, int]:
        """获取按状态聚合的知识缺口计数。"""
        stmt = (
            select(KnowledgeGap.status, func.count())
            .where(KnowledgeGap.tenant_id == tenant_id)
            .group_by(KnowledgeGap.status)
        )
        result = await self.db.execute(stmt)
        counts = dict(result.all())

        return {
            "open": counts.get("open", 0),
            "document_needed": counts.get("document_needed", 0),
            "uploaded": counts.get("uploaded", 0),
            "verified": counts.get("verified", 0),
            "ignored": counts.get("ignored", 0),
            "total": sum(counts.values()),
        }
