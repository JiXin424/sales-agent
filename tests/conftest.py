"""测试配置。"""

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sales_agent.models.agent_run import AgentRun
from sales_agent.models.feedback import Feedback

# 测试用数据库 URL —— 独立库，避免测试的 drop_all 清空开发/生产库 sales_agent。
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test",
)


@pytest.fixture(scope="session")
def event_loop():
    """创建 session 级别的事件循环。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    """创建测试用数据库 session，每个测试后回滚。"""
    from sales_agent.core.database import Base

    engine = create_async_engine(TEST_DB_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        yield session
        await session.rollback()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def sample_tenant(db_session: AsyncSession):
    """创建一个测试租户，返回 tenant_id。"""
    from sales_agent.models.tenant import Tenant
    import json

    tenant = Tenant(
        id="test_tenant_001",
        name="Test Tenant",
        status="active",
        config_json=json.dumps({
            "model": {
                "provider": "openai_compatible",
                "base_url": "https://api.test.com/v1",
                "chat_model": "test-model",
                "embedding_model": "test-embedding",
            },
        }, ensure_ascii=False),
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant.id


@pytest_asyncio.fixture
async def sample_conversation(db_session: AsyncSession, sample_tenant: str):
    """创建一个测试会话，返回 conversation_id。"""
    from sales_agent.models.conversation import Conversation

    conv = Conversation(
        id="test_conv_001",
        tenant_id=sample_tenant,
        user_id="test_user_001",
        channel="local",
        message="测试消息",
        task_type="knowledge_qa",
        task_confidence=0.9,
        answer="测试回答",
        status="completed",
    )
    db_session.add(conv)
    await db_session.flush()
    return conv.id


@pytest_asyncio.fixture
async def sample_feedback(db_session: AsyncSession, sample_tenant: str, sample_conversation: str):
    """创建一个测试反馈，返回 feedback 对象。"""
    fb = Feedback(
        id="test_fb_001",
        tenant_id=sample_tenant,
        conversation_id=sample_conversation,
        user_id="test_user_001",
        rating="down",
        feedback_text="回答不准确",
        review_status="open",
    )
    db_session.add(fb)
    await db_session.flush()
    return fb


@pytest_asyncio.fixture
async def active_agent(db_session: AsyncSession, sample_tenant: str):
    """Create a test Agent, return the Agent object."""
    from sales_agent.models.agent import Agent

    agent = Agent(
        id="test_agent_001",
        tenant_id=sample_tenant,
        name="Test Agent",
        status="active",
        feature_flags_json="{}",
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def other_agent(db_session: AsyncSession):
    """Create a second test Agent in a different tenant."""
    from sales_agent.models.tenant import Tenant
    from sales_agent.models.agent import Agent
    import json

    tenant = Tenant(
        id="test_tenant_002",
        name="Other Tenant",
        status="active",
        config_json=json.dumps({
            "model": {
                "provider": "openai_compatible",
                "base_url": "https://api.test.com/v1",
                "chat_model": "test-model",
                "embedding_model": "test-embedding",
            },
        }, ensure_ascii=False),
    )
    db_session.add(tenant)

    agent = Agent(
        id="test_agent_002",
        tenant_id="test_tenant_002",
        name="Other Agent",
        status="active",
        feature_flags_json="{}",
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def sample_agent_run(db_session: AsyncSession, sample_tenant: str, sample_conversation: str):
    """创建一个测试 Agent Run，返回 agent_run 对象。"""
    run = AgentRun(
        id="test_run_001",
        tenant_id=sample_tenant,
        conversation_id=sample_conversation,
        user_id="test_user_001",
        task_type="knowledge_qa",
        path="standard",
        status="completed",
        total_latency_ms=500,
        route_confidence=0.9,
    )
    db_session.add(run)
    await db_session.flush()
    return run
