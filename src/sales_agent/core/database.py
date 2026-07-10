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

    顺序契约：本函数在 ``init_db`` 里于 ``Base.metadata.create_all`` **之前**调用。
    - 老库（有 alembic_version）：upgrade head 时 create_all 尚未预建任何表，migration
      里的 create_table 不会撞 DuplicateTableError，add_column 也能正常执行；随后 init_db
      末尾的 create_all 再幂等补建 model-only 表。
    - 新库（无 alembic_version）：stamp head（不跑 upgrade），随后 create_all 基于最新
      model 一次性建好全部表。

    历史 bug：旧版本 create_all 在 upgrade 之前跑，会抢建 migration 里的新表（model 已有），
    导致 upgrade 撞 DuplicateTableError → 兜底 stamp head → 跳过同 migration 的 add_column，
    形成「alembic_version 标到 head、列却没加上」的幽灵漂移。被这种漂移污染过的库需用
    backfill migration（如 0012_backfill_skipped_columns）补回缺失列。

    局限：若某张表在「历史上」已被 create_all 预建（model 早于其 create_table migration
    引入），upgrade 重跑该 migration 仍会撞 → 走兜底 stamp。彻底防范有赖于「新 model 表
    随即配 create_table migration」的规范，以及 CI 的 schema 一致性校验。

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
        except Exception as upgrade_exc:
            # upgrade 失败时 alembic_version 停在最后一个成功 revision；为让后续 revision
            # 仍能推进而 stamp head。但 stamp head 会掩盖「失败 revision 里的 add_column 未
            # 执行」——必须用 backfill migration 补齐，否则会再现 prod3 式的幽灵漂移
            # （alembic_version=head 但列缺失）。这里把异常打全，便于在日志里发现漂移。
            log.warning(
                "Auto alembic upgrade failed (%s); stamping head as fallback so later "
                "revisions can proceed. WARNING: any add_column in the failing revision "
                "was SKIPPED — backfill it with a dedicated migration or columns will drift",
                upgrade_exc,
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

    # 先跑 alembic 自动迁移，再 create_all 补建 model-only 表。顺序不能反：
    # 若 create_all 先跑，会抢建「同时出现在某个 migration create_table 里」的表（model
    # 已有该表），导致随后的 alembic upgrade 撞 DuplicateTableError、触发 stamp head 兜底、
    # 跳过同一 migration 里的 add_column —— 老表的新列永远加不上。曾因此导致 prod3 的
    # conversation_messages.topic_id 缺失、钉钉 stream 事务持续 aborted。详见
    # _run_auto_migrations 与 changelog/2026-07-06.md。
    await _run_auto_migrations(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
