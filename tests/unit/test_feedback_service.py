"""Feedback Service 单元测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.services.feedback_service import FeedbackService


class TestFeedbackService:
    @pytest.mark.asyncio
    async def test_submit_creates_row(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """提交反馈应创建 Feedback 记录。"""
        svc = FeedbackService(db_session)
        fb = await svc.submit(
            tenant_id=sample_tenant,
            conversation_id=sample_conversation,
            user_id="user_001",
            rating="up",
            feedback_text="很有帮助",
            labels=["accurate"],
        )

        assert fb.id is not None
        assert fb.tenant_id == sample_tenant
        assert fb.conversation_id == sample_conversation
        assert fb.rating == "up"
        assert fb.feedback_text == "很有帮助"

    @pytest.mark.asyncio
    async def test_submit_rejects_unknown_conversation(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """不存在的会话应被拒绝。"""
        svc = FeedbackService(db_session)
        with pytest.raises(ValueError, match="Conversation not found"):
            await svc.submit(
                tenant_id=sample_tenant,
                conversation_id="nonexistent_conv",
                user_id="user_001",
                rating="up",
            )

    @pytest.mark.asyncio
    async def test_submit_rejects_invalid_rating(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """非法 rating 应被拒绝。"""
        svc = FeedbackService(db_session)
        with pytest.raises(ValueError, match="Invalid rating"):
            await svc.submit(
                tenant_id=sample_tenant,
                conversation_id=sample_conversation,
                user_id="user_001",
                rating="meh",
            )

    @pytest.mark.asyncio
    async def test_get_by_conversation(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """按会话查询反馈。"""
        svc = FeedbackService(db_session)

        # 提交多条反馈
        await svc.submit(sample_tenant, sample_conversation, "user_001", "up", "好")
        await svc.submit(sample_tenant, sample_conversation, "user_002", "down", "差")

        fbs = await svc.get_by_conversation(sample_tenant, sample_conversation)
        assert len(fbs) == 2

    @pytest.mark.asyncio
    async def test_list_by_tenant(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """按租户查询反馈，支持过滤和分页。"""
        svc = FeedbackService(db_session)

        await svc.submit(sample_tenant, sample_conversation, "u1", "up")
        await svc.submit(sample_tenant, sample_conversation, "u2", "down")
        await svc.submit(sample_tenant, sample_conversation, "u3", "up")

        # 全部
        items, total = await svc.list_by_tenant(sample_tenant)
        assert total == 3
        assert len(items) == 3

        # 按 rating 过滤
        ups, up_total = await svc.list_by_tenant(sample_tenant, rating="up")
        assert up_total == 2

        # 分页
        page1, _ = await svc.list_by_tenant(sample_tenant, limit=2, offset=0)
        assert len(page1) == 2

    @pytest.mark.asyncio
    async def test_get_summary(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """反馈汇总应正确计数。"""
        svc = FeedbackService(db_session)

        await svc.submit(sample_tenant, sample_conversation, "u1", "up")
        await svc.submit(sample_tenant, sample_conversation, "u2", "down")
        await svc.submit(sample_tenant, sample_conversation, "u3", "up")

        summary = await svc.get_summary(sample_tenant)
        assert summary["up"] == 2
        assert summary["down"] == 1
        assert summary["total"] == 3

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """不同租户不应看到彼此的反馈。"""
        svc = FeedbackService(db_session)
        await svc.submit(sample_tenant, sample_conversation, "u1", "up")

        # 其他租户查询应为空
        items, total = await svc.list_by_tenant("other_tenant_999")
        assert total == 0
        assert items == []
