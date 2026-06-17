"""钉钉消息速率限制。"""

from __future__ import annotations

import time
from collections import defaultdict

from sales_agent.integrations.dingtalk.config import DingTalkConfig, DingTalkRateLimitConfig


class InMemoryRateLimiter:
    """基于滑动窗口的内存速率限制器。"""

    def __init__(self, config: DingTalkRateLimitConfig):
        self._config = config
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str, tenant_id: str) -> tuple[bool, str | None]:
        """检查请求是否在速率限制内。

        Returns:
            (allowed, error_message) — 如果 allowed=False，error_message 说明原因
        """
        now = time.time()

        # 检查 per-tenant-per-minute
        tenant_key = f"tenant:{tenant_id}:minute"
        tenant_count = self._count_in_window(tenant_key, now, 60)
        if tenant_count >= self._config.per_tenant_per_minute:
            return False, "当前系统处理请求较多，请稍后再试。"

        # 检查 per-user-per-minute
        user_min_key = f"user:{user_id}:minute"
        user_min_count = self._count_in_window(user_min_key, now, 60)
        if user_min_count >= self._config.per_user_per_minute:
            return False, "你发送得有点快，我先暂停处理一下。请稍后再试。"

        # 检查 per-user-per-day
        user_day_key = f"user:{user_id}:day"
        user_day_count = self._count_in_window(user_day_key, now, 86400)
        if user_day_count >= self._config.per_user_per_day:
            return False, "今天的消息数量已达到上限，请明天再试。"

        # 通过检查，记录本次请求
        self._windows[tenant_key].append(now)
        self._windows[user_min_key].append(now)
        self._windows[user_day_key].append(now)

        return True, None

    def _count_in_window(self, key: str, now: float, window_seconds: float) -> int:
        """计算窗口内的请求数（同时清理过期条目）。"""
        timestamps = self._windows[key]
        cutoff = now - window_seconds
        # 过滤过期
        self._windows[key] = [t for t in timestamps if t > cutoff]
        return len(self._windows[key])


# 模块级单例
_limiter: InMemoryRateLimiter | None = None


def get_rate_limiter(config: DingTalkConfig) -> InMemoryRateLimiter:
    """获取或创建速率限制器单例。"""
    global _limiter
    if _limiter is None:
        _limiter = InMemoryRateLimiter(config.rate_limit)
    return _limiter
