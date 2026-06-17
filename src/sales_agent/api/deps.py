"""API 依赖注入。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import Settings, get_settings
from sales_agent.core.database import get_db


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库 session。"""
    async for session in get_db():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
