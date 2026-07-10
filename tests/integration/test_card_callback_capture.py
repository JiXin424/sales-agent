"""钉钉卡片按钮回调采集（capture-first）。

- capture_card_callback：真实 DB 落库，中文不转义、字段正确。
- SalesAgentCardCallbackHandler.process：防御式契约——畸形载荷 / DB 故障
  都不外抛、恒返回 STATUS_OK（全租户优雅降级的核心保证）。
"""

from __future__ import annotations

import json
import os

import dingtalk_stream
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sales_agent.integrations.dingtalk.card_callback_handler import (
    SalesAgentCardCallbackHandler,
    capture_card_callback,
)
from sales_agent.integrations.dingtalk.models import DingTalkCardCallbackEvent

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test",
)


class FakeCallback:
    """最小 CallbackMessage 替身——process 只访问 .data。"""

    def __init__(self, data):
        self.data = data


@pytest_asyncio.fixture
async def db_session():
    from sales_agent.core.database import Base
    import sales_agent.models  # noqa: F401 — 注册核心模型

    sales_agent.models._import_dingtalk_models()  # 注册钉钉模型（含本表）

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_capture_persists_raw_payload_with_chinese(db_session: AsyncSession):
    raw = {
        "corpId": "corp_1",
        "userId": "user_123",
        "outTrackId": "stream_abc123",
        "content": json.dumps(
            {"rating": "down", "problems": ["不准确", "太啰嗦"]}, ensure_ascii=False
        ),
        "extension": json.dumps({"src": "im_robot"}, ensure_ascii=False),
    }
    msg = dingtalk_stream.CardCallbackMessage.from_dict(raw)

    await capture_card_callback(db_session, msg, "tenant_x", raw)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(DingTalkCardCallbackEvent).where(
                DingTalkCardCallbackEvent.tenant_id == "tenant_x"
            )
        )
    ).scalar_one()

    assert row.dingtalk_user_id == "user_123"
    assert row.card_instance_id == "stream_abc123"
    assert row.corp_id == "corp_1"
    assert row.processed is False
    # 中文不转义 + 结构完整
    assert "不准确" in row.content_json
    content = json.loads(row.content_json)
    assert content["rating"] == "down"
    assert content["problems"] == ["不准确", "太啰嗦"]
    assert json.loads(row.extension_json)["src"] == "im_robot"
    assert json.loads(row.raw_json)["userId"] == "user_123"


@pytest.mark.asyncio
async def test_process_malformed_content_does_not_raise():
    """content 非法 JSON → from_dict 抛错 → 恒 STATUS_OK、不落库。"""
    handler = SalesAgentCardCallbackHandler("tenant_x")
    code, resp = await handler.process(FakeCallback({"content": "not-a-json"}))
    assert code == dingtalk_stream.AckMessage.STATUS_OK
    assert resp == "parse_error"


@pytest.mark.asyncio
async def test_process_non_dict_data_does_not_raise():
    handler = SalesAgentCardCallbackHandler("tenant_x")
    code, _ = await handler.process(FakeCallback(None))
    assert code == dingtalk_stream.AckMessage.STATUS_OK


@pytest.mark.asyncio
async def test_process_db_failure_still_acks(monkeypatch):
    """DB 不可用（get_session_factory 抛错）→ 仍恒 STATUS_OK。"""

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("sales_agent.core.database.get_session_factory", boom)

    handler = SalesAgentCardCallbackHandler("tenant_x")
    code, resp = await handler.process(
        FakeCallback({"userId": "u1", "content": json.dumps({}), "extension": json.dumps({})})
    )
    assert code == dingtalk_stream.AckMessage.STATUS_OK
    assert resp == "OK"
