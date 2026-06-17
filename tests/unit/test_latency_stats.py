"""LatencyStatsCollector 单元测试。"""

import pytest

from sales_agent.services.latency_stats import LatencyStatsCollector


class TestLatencyStatsCollector:
    """延迟统计收集器测试。"""

    @pytest.mark.asyncio
    async def test_record_and_get(self):
        """记录样本并获取百分位。"""
        collector = LatencyStatsCollector(window_size=100)
        for i in range(100):
            await collector.record("fast", float(i))

        stats = collector.get_percentiles("fast")
        assert stats["fast"]["p50"] > 0
        assert stats["fast"]["p90"] > stats["fast"]["p50"]
        assert stats["fast"]["p95"] > stats["fast"]["p90"]
        assert stats["fast"]["count"] == 100

    @pytest.mark.asyncio
    async def test_multiple_paths(self):
        """多条路径独立统计。"""
        collector = LatencyStatsCollector()
        await collector.record("fast", 100.0)
        await collector.record("fast", 200.0)
        await collector.record("standard", 3000.0)
        await collector.record("standard", 6000.0)

        stats = collector.get_summary()
        assert "fast" in stats
        assert "standard" in stats
        assert stats["fast"]["count"] == 2
        assert stats["standard"]["count"] == 2

    @pytest.mark.asyncio
    async def test_empty_path_returns_zero(self):
        """无数据的路径返回 0。"""
        collector = LatencyStatsCollector()
        stats = collector.get_percentiles("nonexistent")
        assert stats["nonexistent"]["p50"] == 0
        assert stats["nonexistent"]["count"] == 0

    @pytest.mark.asyncio
    async def test_window_size_eviction(self):
        """超过窗口大小时淘汰旧样本。"""
        collector = LatencyStatsCollector(window_size=10)
        for i in range(20):
            await collector.record("fast", float(i))

        stats = collector.get_percentiles("fast")
        assert stats["fast"]["count"] == 20  # total_count 不受窗口限制
        assert stats["fast"]["current_window"] == 10  # 窗口内只有 10 个

    @pytest.mark.asyncio
    async def test_percentile_calculation(self):
        """百分位数计算精度。"""
        collector = LatencyStatsCollector(window_size=1000)
        # 100 个样本，0-99ms
        for i in range(100):
            await collector.record("test", float(i))

        stats = collector.get_percentiles("test")
        # p50 应该接近 49.5
        assert 45 < stats["test"]["p50"] < 55
        # p90 应该接近 89.1
        assert 85 < stats["test"]["p90"] < 95
        # p95 应该接近 94.05
        assert 90 < stats["test"]["p95"] < 99
