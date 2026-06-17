"""ChatPipeline 单元测试 — 覆盖快速命令、路径选择、处理中提示。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sales_agent.services.chat_pipeline import (
    ChatPipeline,
    _ProcessingNoticeGuard,
    _HELP_COMMANDS,
    _RESET_COMMANDS,
)
from sales_agent.core.config import Settings, LatencyConfig, PathRouterConfig


class TestFastCommands:
    """快速命令直接返回，不调 LLM/RAG/risk。"""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def settings(self):
        s = Settings()
        s.latency = LatencyConfig()
        s.path_router = PathRouterConfig()
        return s

    @pytest.mark.asyncio
    async def test_help_returns_fast(self, mock_db, settings):
        """帮助命令直接返回，0 次 LLM 调用。"""
        pipeline = ChatPipeline(mock_db, settings)
        result = await pipeline.execute(
            tenant_id="t1",
            user_id="u1",
            message="帮助",
            conversation_id="conv1",
        )
        assert result.fast_reply is not None
        assert result.path_result.path == "fast"
        assert "你可以直接问" in result.fast_reply
        assert len(result.timings.stages) > 0

    @pytest.mark.asyncio
    async def test_help_english(self, mock_db, settings):
        pipeline = ChatPipeline(mock_db, settings)
        result = await pipeline.execute(
            tenant_id="t1", user_id="u1",
            message="help", conversation_id="conv1",
        )
        assert result.fast_reply is not None
        assert result.path_result.path == "fast"

    @pytest.mark.asyncio
    async def test_reset_returns_fast(self, mock_db, settings):
        """重置命令返回新 conversation_id。"""
        pipeline = ChatPipeline(mock_db, settings)
        result = await pipeline.execute(
            tenant_id="t1", user_id="u1",
            message="新话题", conversation_id="conv1",
        )
        assert result.fast_reply is not None
        assert result.path_result.path == "fast"
        assert result.conversation_id != "conv1"  # 新 ID

    @pytest.mark.asyncio
    async def test_reset_slash(self, mock_db, settings):
        pipeline = ChatPipeline(mock_db, settings)
        result = await pipeline.execute(
            tenant_id="t1", user_id="u1",
            message="/reset", conversation_id="conv1",
        )
        assert result.fast_reply is not None
        assert result.path_result.path == "fast"

    @pytest.mark.asyncio
    async def test_question_mark_fast(self, mock_db, settings):
        pipeline = ChatPipeline(mock_db, settings)
        result = await pipeline.execute(
            tenant_id="t1", user_id="u1",
            message="？", conversation_id="conv1",
        )
        assert result.fast_reply is not None


class TestProcessingNoticeGuard:
    """处理中提示的生命周期测试。"""

    @pytest.mark.asyncio
    async def test_notice_fires_after_threshold(self):
        """超过阈值后发送提示。"""
        sent = []

        async def mock_reply(text):
            sent.append(text)

        guard = _ProcessingNoticeGuard(mock_reply, threshold_seconds=0.05)
        await guard.start()
        # 等待超过阈值
        await asyncio.sleep(0.1)
        assert len(sent) == 1
        assert "稍等" in sent[0]

    @pytest.mark.asyncio
    async def test_notice_cancelled_before_threshold(self):
        """阈值内取消不发送提示。"""
        sent = []

        async def mock_reply(text):
            sent.append(text)

        guard = _ProcessingNoticeGuard(mock_reply, threshold_seconds=0.5)
        await guard.start()
        # 立即取消
        guard.cancel()
        await asyncio.sleep(0.6)
        assert len(sent) == 0

    @pytest.mark.asyncio
    async def test_cancel_after_fire_is_noop(self):
        """发送后取消不会报错。"""
        sent = []

        async def mock_reply(text):
            sent.append(text)

        guard = _ProcessingNoticeGuard(mock_reply, threshold_seconds=0.05)
        await guard.start()
        await asyncio.sleep(0.1)
        guard.cancel()  # 应该不报错
        assert len(sent) == 1
