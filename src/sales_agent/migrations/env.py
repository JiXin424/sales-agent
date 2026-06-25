"""Alembic 环境配置（async engine）。

数据库 URL 注入顺序：``DATABASE_URL`` 环境变量 → ``config/default.yaml`` 里的
``database.url``（默认 ``postgresql+asyncpg://...``）。运行：在项目根目录执行
``alembic upgrade head`` / ``alembic stamp head`` 等。
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 注册所有 ORM 模型到 Base.metadata（含延迟导入的钉钉模型）
from sales_agent.core.database import Base

import sales_agent.models  # noqa: F401
sales_agent.models._import_dingtalk_models()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注入数据库 URL：优先 DATABASE_URL 环境变量，否则用应用 settings
db_url = os.getenv("DATABASE_URL")
if not db_url:
    from sales_agent.core.config import get_settings

    db_url = get_settings().database.url
elif db_url.startswith("postgresql://"):
    # 兼容同步 driver 写法，强制换成 asyncpg
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：连库执行（async）。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
