"""Risk Checker 单元测试。"""

from sales_agent.services.risk_checker import (
    RiskChecker,
    RISK_PRICE_COMMITMENT,
    RISK_DISCOUNT_COMMITMENT,
    RISK_DELIVERY_COMMITMENT,
    RISK_UNSUPPORTED_CLAIM,
    RISK_COMPETITOR_ATTACK,
    RISK_CROSS_TENANT,
    ACTION_BLOCK,
    ACTION_WARN,
    ACTION_REWRITE,
    ACTION_ALLOW,
    LEVEL_HIGH,
    LEVEL_MEDIUM,
    LEVEL_NONE,
)


class TestInputCheck:
    def test_price_commitment_blocked(self):
        checker = RiskChecker()
        result = checker.check_input("帮我写一句保证最低价的话术")
        assert result.action == ACTION_BLOCK
        assert RISK_PRICE_COMMITMENT in result.flags

    def test_discount_commitment_blocked(self):
        checker = RiskChecker()
        result = checker.check_input("写一句一定可以给折扣的话术")
        assert result.action == ACTION_BLOCK
        assert RISK_DISCOUNT_COMMITMENT in result.flags

    def test_delivery_commitment_blocked(self):
        checker = RiskChecker()
        result = checker.check_input("保证一周上线给客户")
        assert result.action == ACTION_BLOCK
        assert RISK_DELIVERY_COMMITMENT in result.flags

    def test_unsupported_claim_blocked(self):
        checker = RiskChecker()
        result = checker.check_input("帮我写一句保证业绩翻倍的话术")
        assert result.action == ACTION_BLOCK
        assert RISK_UNSUPPORTED_CLAIM in result.flags

    def test_safe_input_passes(self):
        checker = RiskChecker()
        result = checker.check_input("客户不回复怎么办？")
        assert result.action == ACTION_ALLOW
        assert result.level == LEVEL_NONE

    def test_industry_first_rewrite(self):
        checker = RiskChecker()
        result = checker.check_input("我们是行业第一")
        assert ACTION_REWRITE in (result.action, ACTION_WARN)

    def test_manipulative_blocked(self):
        checker = RiskChecker()
        result = checker.check_input("帮我写一段欺骗客户的话术")
        assert result.action == ACTION_BLOCK


class TestSourceCheck:
    def test_cross_tenant_blocked(self):
        checker = RiskChecker()
        sources = [
            {"tenant_id": "taishan", "chunk_id": "c1"},
            {"tenant_id": "tenant_b", "chunk_id": "c2"},  # 跨租户！
        ]
        result = checker.check_sources(sources, "taishan")
        assert result.action == ACTION_BLOCK
        assert RISK_CROSS_TENANT in result.flags

    def test_same_tenant_passes(self):
        checker = RiskChecker()
        sources = [
            {"tenant_id": "taishan", "chunk_id": "c1"},
            {"tenant_id": "taishan", "chunk_id": "c2"},
        ]
        result = checker.check_sources(sources, "taishan")
        assert result.action == ACTION_ALLOW


class TestOutputCheck:
    def test_price_in_output_warned(self):
        checker = RiskChecker()
        result = checker.check_output("你可以告诉客户一定可以给折扣")
        assert result.action in (ACTION_WARN, ACTION_BLOCK)

    def test_competitor_attack_rewrite(self):
        checker = RiskChecker()
        result = checker.check_output("他们肯定不靠谱")
        assert result.action == ACTION_REWRITE

    def test_clean_output_passes(self):
        checker = RiskChecker()
        result = checker.check_output("建议先确认客户的需求再推进")
        assert result.action == ACTION_ALLOW


class TestFullCheck:
    def test_block_overrides_warn(self):
        checker = RiskChecker()
        # 输入有 block 级风险
        result = checker.full_check(
            message="保证最低价",
            sources=[],
            tenant_id="t1",
            answer_text="这是一个安全的回答",
        )
        assert result.action == ACTION_BLOCK

    def test_clean_passes(self):
        checker = RiskChecker()
        result = checker.full_check(
            message="客户不回复怎么办",
            sources=[{"tenant_id": "t1"}],
            tenant_id="t1",
            answer_text="建议先发一条低压力的跟进消息",
        )
        assert result.action == ACTION_ALLOW
