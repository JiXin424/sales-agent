"""钉钉用户映射 — 将钉钉用户映射到内部用户。"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.integrations.dingtalk.models import DingTalkUserMapping
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


class DingTalkUserMapper:
    """钉钉用户映射器。"""

    def __init__(self, db: AsyncSession, tenant_id: str):
        self._db = db
        self._tenant_id = tenant_id

    async def get_or_create_user(
        self,
        corp_id: str,
        dingtalk_user_id: str,
        display_name: str = "",
    ) -> str:
        """获取或创建内部用户 ID。

        Args:
            corp_id: 钉钉企业 ID
            dingtalk_user_id: 钉钉用户 ID
            display_name: 显示名称

        Returns:
            内部用户 ID
        """
        # 查找已有映射
        stmt = select(DingTalkUserMapping).where(
            DingTalkUserMapping.tenant_id == self._tenant_id,
            DingTalkUserMapping.dingtalk_user_id == dingtalk_user_id,
            DingTalkUserMapping.status == "active",
        )
        result = await self._db.execute(stmt)
        mapping = result.scalar_one_or_none()

        if mapping:
            return mapping.internal_user_id

        # 自动创建
        internal_user_id = f"ding_{generate_id()}"
        mapping = DingTalkUserMapping(
            id=generate_id(),
            tenant_id=self._tenant_id,
            dingtalk_corp_id=corp_id,
            dingtalk_user_id=dingtalk_user_id,
            internal_user_id=internal_user_id,
            display_name=display_name,
            status="active",
        )
        self._db.add(mapping)
        await self._db.flush()

        logger.info(
            "Created DingTalk user mapping: tenant=%s dingtalk=%s -> internal=%s",
            self._tenant_id, dingtalk_user_id, internal_user_id,
        )
        return internal_user_id
