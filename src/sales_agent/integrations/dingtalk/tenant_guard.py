"""钉钉租户校验 — dedicated mode 下验证 corp_id 匹配。"""

from __future__ import annotations

import logging

from sales_agent.core.exceptions import DingTalkTenantMismatchError
from sales_agent.integrations.dingtalk.config import DingTalkConfig

logger = logging.getLogger(__name__)


class DingTalkTenantGuard:
    """钉钉租户校验器。"""

    def __init__(self, config: DingTalkConfig):
        self._expected_corp_id = config.corp_id

    def verify_corp(self, corp_id: str) -> None:
        """校验 corp_id 与当前实例配置是否匹配。

        Args:
            corp_id: 事件中的企业 ID

        Raises:
            DingTalkTenantMismatchError: 不匹配时抛出
        """
        if not corp_id:
            logger.warning("DingTalk event missing corp_id, rejecting")
            raise DingTalkTenantMismatchError(corp_id="")

        if not self._expected_corp_id:
            logger.warning("Instance DINGTALK_CORP_ID not configured, rejecting all events")
            raise DingTalkTenantMismatchError(corp_id=corp_id)

        if corp_id != self._expected_corp_id:
            logger.warning(
                "DingTalk corp_id mismatch: event=%s, expected=%s",
                corp_id,
                self._expected_corp_id,
            )
            raise DingTalkTenantMismatchError(corp_id=corp_id)
