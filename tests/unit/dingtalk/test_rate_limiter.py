"""钉钉速率限制单元测试。"""

import time
from unittest.mock import patch

from sales_agent.integrations.dingtalk.config import DingTalkConfig, DingTalkRateLimitConfig
from sales_agent.integrations.dingtalk.rate_limiter import InMemoryRateLimiter


class TestInMemoryRateLimiter:

    def _make_limiter(self, **kwargs) -> InMemoryRateLimiter:
        config = DingTalkRateLimitConfig(
            per_user_per_minute=5,
            per_user_per_day=100,
            per_tenant_per_minute=50,
            **kwargs,
        )
        return InMemoryRateLimiter(config)

    def test_under_limit_passes(self):
        limiter = self._make_limiter()
        allowed, error = limiter.check("user_001", "corp_001")
        assert allowed is True
        assert error is None

    def test_per_user_per_minute_limit(self):
        limiter = self._make_limiter()
        # 消耗 5 次
        for _ in range(5):
            limiter.check("user_001", "corp_001")
        # 第 6 次应该被限制
        allowed, error = limiter.check("user_001", "corp_001")
        assert allowed is False
        assert error is not None
        assert "快" in error

    def test_per_user_per_day_limit(self):
        config = DingTalkRateLimitConfig(
            per_user_per_minute=200,  # 提高分钟限制
            per_user_per_day=3,
            per_tenant_per_minute=500,
        )
        limiter = InMemoryRateLimiter(config)
        for _ in range(3):
            allowed, _ = limiter.check("user_001", "corp_001")
            assert allowed is True
        # 第 4 次被日限
        allowed, error = limiter.check("user_001", "corp_001")
        assert allowed is False
        assert "上限" in error

    def test_different_users_independent(self):
        limiter = self._make_limiter()
        # user_001 消耗 5 次
        for _ in range(5):
            limiter.check("user_001", "corp_001")
        # user_002 应该不受影响
        allowed, _ = limiter.check("user_002", "corp_001")
        assert allowed is True

    def test_expired_entries_pruned(self):
        limiter = self._make_limiter()
        now = time.time()
        # 模拟 60 秒前的请求
        with patch("time.time", return_value=now - 60):
            for _ in range(5):
                limiter.check("user_001", "corp_001")
        # 现在再请求应该通过（旧条目已过期）
        with patch("time.time", return_value=now):
            allowed, _ = limiter.check("user_001", "corp_001")
            assert allowed is True

    def test_per_tenant_limit(self):
        config = DingTalkRateLimitConfig(
            per_user_per_minute=200,
            per_tenant_per_minute=3,
        )
        limiter = InMemoryRateLimiter(config)
        # 不同用户但同一租户
        limiter.check("user_001", "corp_001")
        limiter.check("user_002", "corp_001")
        limiter.check("user_003", "corp_001")
        # 第 4 次被租户限制
        allowed, error = limiter.check("user_004", "corp_001")
        assert allowed is False
        assert "系统" in error
