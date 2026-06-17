"""钉钉用户映射单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sales_agent.integrations.dingtalk.user_mapper import DingTalkUserMapper


def _make_db(scalar_result=None):
    """创建 mock db，add/flush 是同步方法。"""
    db = MagicMock()
    db.execute = AsyncMock(return_value=scalar_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestDingTalkUserMapper:

    @pytest.mark.asyncio
    async def test_create_new_user(self):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db = _make_db(mock_result)

        mapper = DingTalkUserMapper(db, "tenant_001")
        user_id = await mapper.get_or_create_user(
            corp_id="corp_001",
            dingtalk_user_id="ding_user_001",
            display_name="张三",
        )
        assert user_id.startswith("ding_")
        assert db.add.called
        assert db.flush.called

    @pytest.mark.asyncio
    async def test_return_existing_user(self):
        existing = MagicMock()
        existing.internal_user_id = "ding_existing_123"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db = _make_db(mock_result)

        mapper = DingTalkUserMapper(db, "tenant_001")
        user_id = await mapper.get_or_create_user(
            corp_id="corp_001", dingtalk_user_id="ding_user_001",
        )
        assert user_id == "ding_existing_123"
        assert not db.add.called

    @pytest.mark.asyncio
    async def test_different_users_get_different_ids(self):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db = _make_db(mock_result)

        mapper = DingTalkUserMapper(db, "tenant_001")
        uid1 = await mapper.get_or_create_user(corp_id="corp_001", dingtalk_user_id="u1")
        uid2 = await mapper.get_or_create_user(corp_id="corp_001", dingtalk_user_id="u2")
        assert uid1 != uid2
