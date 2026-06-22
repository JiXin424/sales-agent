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
            pool_size=5,
            max_overflow=10,
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

    自动处理 baseline stamp（alembic_version 表不存在时）：
    - 新库（create_all 刚建、schema 最新）→ stamp head；
    - 旧库（schema 是 baseline）→ stamp baseline 再 upgrade head。
    凭 ``prompt_versions`` 是否有 ``prompt_category`` 列区分新旧。
    alembic_version 表已存在则直接 upgrade head（幂等）。
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
                has_new_col = bool(
                    (
                        await conn.execute(
                            text(
                                "SELECT EXISTS (SELECT FROM information_schema.columns "
                                "WHERE table_name='prompt_versions' "
                                "AND column_name='prompt_category')"
                            )
                        )
                    ).scalar()
                )
                # 新库 create_all 已建最新 schema → stamp head；旧库 → stamp baseline
                stamp_rev = "0002_prompt_category" if has_new_col else "0001_baseline"
                log.info("Auto alembic stamp %s (schema_is_new=%s)", stamp_rev, has_new_col)
                await asyncio.to_thread(command.stamp, cfg, stamp_rev)

        await asyncio.to_thread(command.upgrade, cfg, "head")
        log.info("Auto alembic upgrade to head completed")
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
