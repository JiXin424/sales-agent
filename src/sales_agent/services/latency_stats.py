"""延迟统计收集器 — 滑动窗口 p50/p90/p95 百分位统计。

对应 spec §13 监控指标。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PathStats:
    """单条路径的统计数据。"""

    samples: deque = field(default_factory=lambda: deque(maxlen=1000))
    total_count: int = 0


class LatencyStatsCollector:
    """滑动窗口百分位统计收集器。

    按 path (fast/standard/slow) 分组收集延迟样本，
    计算实时 p50/p90/p95。
    """

    def __init__(self, window_size: int = 1000):
        self._window_size = window_size
        self._stats: dict[str, PathStats] = {}
        self._lock = asyncio.Lock()

    async def record(self, path: str, latency_ms: float) -> None:
        """记录一次请求的延迟。"""
        async with self._lock:
            if path not in self._stats:
                self._stats[path] = PathStats(
                    samples=deque(maxlen=self._window_size)
                )
            self._stats[path].samples.append(latency_ms)
            self._stats[path].total_count += 1

    def get_percentiles(self, path: str | None = None) -> dict[str, dict]:
        """获取百分位统计。

        Args:
            path: 指定路径，None 返回所有路径

        Returns:
            dict of {path: {"p50": ..., "p90": ..., "p95": ..., "count": ...}}
        """
        if path:
            paths = {path: self._stats.get(path)}
        else:
            paths = dict(self._stats)

        result = {}
        for p, stats in paths.items():
            if not stats or not stats.samples:
                result[p] = {"p50": 0, "p90": 0, "p95": 0, "count": 0}
                continue

            sorted_samples = sorted(stats.samples)
            n = len(sorted_samples)
            result[p] = {
                "p50": self._percentile(sorted_samples, 50),
                "p90": self._percentile(sorted_samples, 90),
                "p95": self._percentile(sorted_samples, 95),
                "count": stats.total_count,
                "current_window": n,
            }

        return result

    def get_summary(self) -> dict:
        """获取摘要统计。"""
        return self.get_percentiles()

    @staticmethod
    def _percentile(sorted_data: list, pct: int) -> float:
        """计算百分位数。"""
        if not sorted_data:
            return 0.0
        n = len(sorted_data)
        k = (pct / 100) * (n - 1)
        f = int(k)
        c = f + 1
        if c >= n:
            return round(sorted_data[-1], 1)
        d = k - f
        return round(sorted_data[f] + d * (sorted_data[c] - sorted_data[f]), 1)


# 全局单例
_collector: LatencyStatsCollector | None = None


def get_latency_stats_collector() -> LatencyStatsCollector:
    """获取全局延迟统计收集器。"""
    global _collector
    if _collector is None:
        _collector = LatencyStatsCollector()
    return _collector
