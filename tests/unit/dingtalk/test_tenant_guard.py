"""钉钉租户校验单元测试。"""

import pytest

from sales_agent.core.exceptions import DingTalkTenantMismatchError
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.tenant_guard import DingTalkTenantGuard


class TestDingTalkTenantGuard:

    def test_matching_corp_id_passes(self):
        config = DingTalkConfig(enabled=True, corp_id="corp_abc")
        guard = DingTalkTenantGuard(config)
        # Should not raise
        guard.verify_corp("corp_abc")

    def test_mismatching_corp_id_raises(self):
        config = DingTalkConfig(enabled=True, corp_id="corp_abc")
        guard = DingTalkTenantGuard(config)
        with pytest.raises(DingTalkTenantMismatchError):
            guard.verify_corp("corp_xyz")

    def test_empty_corp_id_rejected(self):
        config = DingTalkConfig(enabled=True, corp_id="corp_abc")
        guard = DingTalkTenantGuard(config)
        with pytest.raises(DingTalkTenantMismatchError):
            guard.verify_corp("")

    def test_no_configured_corp_id_rejects_all(self):
        config = DingTalkConfig(enabled=True, corp_id="")
        guard = DingTalkTenantGuard(config)
        with pytest.raises(DingTalkTenantMismatchError):
            guard.verify_corp("corp_any")
