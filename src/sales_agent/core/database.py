"""异步数据库引擎和 session 管理。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

_engine = None
_session_factory = None


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""
    pass


def get_engine():
    """获取或创建异步引擎。"""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_size=20,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取或创建 session factory。"""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：获取数据库 session。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _run_auto_migrations(engine) -> None:
    """启动时自动把 DB schema 升级到 alembic head。

    注意：``init_db`` 先调了 ``Base.metadata.create_all``（幂等 IF NOT EXISTS），
    所以物理 schema 已经是最新的模型定义。alembic 迁移只需追上版本号，
    不应再跑已执行的 DDL（否则会 DuplicateTableError）。

    流程：
    - 无 alembic_version 表 → 新库，直接 stamp head
    - 有 alembic_version 表 → 先尝试 upgrade head；若因建表冲突失败则 stamp head
    失败不阻断启动（仅警告），保证运维接口可用。
    """
    import asyncio
    import logging
    from pathlib import Path

    log = logging.getLogger(__name__)
    try:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import text
    except ImportError:
        log.warning("alembic not installed, skip auto migration")
        return

    alembic_ini = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not alembic_ini.exists():
        log.warning("alembic.ini not found at %s, skip auto migration", alembic_ini)
        return

    cfg = Config(str(alembic_ini))
    try:
        async with engine.connect() as conn:
            has_version_table = bool(
                (
                    await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT FROM information_schema.tables "
                            "WHERE table_name='alembic_version')"
                        )
                    )
                ).scalar()
            )
            if not has_version_table:
                # 新库：create_all 已经基于最新模型建好全部表 → 直接 stamp head
                log.info("Auto alembic: new DB, stamping head")
                await asyncio.to_thread(command.stamp, cfg, "head")
                log.info("Auto alembic stamp head completed")
                return

        # 表存在 → 先尝试升级；若 create_all 已提前建了迁移里的表则 stamp head 兜底
        try:
            await asyncio.to_thread(command.upgrade, cfg, "head")
            log.info("Auto alembic upgrade to head completed")
        except Exception:
            log.warning(
                "Auto alembic upgrade failed (likely tables already created by "
                "create_all), stamping head as fallback"
            )
            await asyncio.to_thread(command.stamp, cfg, "head")
            log.info("Auto alembic stamp head completed (fallback)")
    except Exception as e:  # noqa: BLE001
        log.warning("Auto alembic migration failed (non-fatal): %s", e)


async def init_db() -> None:
    """初始化数据库：创建缺失的表 + 自动跑 alembic migration。"""
    engine = get_engine()
    # 确保所有模型已导入（含延迟导入的集成模型）
    import sales_agent.models  # noqa: F401
    sales_agent.models._import_dingtalk_models()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 启动时自动把已有表的 schema 升级到 alembic head（部署自带 migration，
    # 无需手动 stamp/upgrade）。详见 _run_auto_migrations。
    await _run_auto_migrations(engine)

    # 为每个 tenant 创建默认 Agent 并回填既有数据（向后兼容）。
    # 失败不阻断启动（仅记录），保证运维接口可用。
    try:
        factory = get_session_factory()
        from sales_agent.services.agent_migration import ensure_default_agents
        from sales_agent.coach.milestones import seed_milestones
        async with factory() as session:
            await ensure_default_agents(session)
            await seed_milestones(session)  # 幂等：84 条里程碑定义
            await session.commit()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "ensure_default_agents skipped during init_db: %s", e
        )


async def close_db() -> None:
    """关闭数据库连接。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
