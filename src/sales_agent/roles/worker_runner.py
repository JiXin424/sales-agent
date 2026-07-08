"""Worker 角色独立运行器。

以 PROCESS_ROLE=worker 运行时，启动后台任务处理进程。
当前实现为最小骨架：初始化数据库 + 保活循环，可扩展为
ingestion jobs、eval runs、alert evaluation、report generation 等。

用法:
    sales-agent worker
    # 或
    PROCESS_ROLE=worker uvicorn sales_agent.main:app
"""

from __future__ import annotations

import asyncio
import logging
import signal

logger = logging.getLogger(__name__)


async def run() -> None:
    """Worker 角色主循环：初始化 → 启动 HTTP Worker（可选） → 保活。"""
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, close_db
    from sales_agent.core.tenant_runtime import get_tenant_runtime
    from sales_agent.services.online_conversation import (
        initialize_online_runtime,
        close_online_runtime,
    )

    settings = get_settings()
    config = settings.dingtalk

    # 初始化数据库
    await init_db()
    logger.info("Database initialized (worker runner)")

    # 初始化 Online Graph runtime
    await initialize_online_runtime()
    logger.info("Online runtime initialized (worker runner)")

    # 加载 TenantRuntime
    runtime = get_tenant_runtime()
    errors = runtime.validate_startup()
    if errors:
        for err in errors:
            logger.error("Startup validation failed: %s", err)
        logger.warning(
            "Worker runner started with %d validation error(s).",
            len(errors),
        )
    else:
        logger.info(
            "Worker runner ready: tenant=%s, mode=%s",
            runtime.tenant_id,
            runtime.deployment_mode,
        )

    # 如果 DingTalk HTTP 模式启用，启动 HTTP Worker
    dt_http_started = False
    if config.enabled and config.message_mode != "stream":
        try:
            from sales_agent.integrations.dingtalk.worker import (
                start_dingtalk_worker,
                stop_dingtalk_worker,
            )
            await start_dingtalk_worker()
            dt_http_started = True
            logger.info("DingTalk HTTP worker started (worker role)")
        except Exception as e:
            logger.warning("Failed to start DingTalk HTTP worker: %s", e)

    # TODO: 在此添加其他后台任务初始化
    # - ingestion job scheduler
    # - eval run scheduler
    # - alert evaluation loop
    # - report generation scheduler

    # Coach 每日评估调度器（Phase 1：手动触发，不自动启动）
    try:
        from sales_agent.coach.scheduler import start_coach_scheduler
        await start_coach_scheduler()
    except Exception as e:
        logger.warning("Coach scheduler init skipped: %s", e)

    logger.info("Worker runner entering keepalive loop")

    # 保活循环：等待取消信号
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Worker runner shutting down...")
        if dt_http_started:
            try:
                await stop_dingtalk_worker()
            except Exception as e:
                logger.warning("Failed to stop DingTalk HTTP worker: %s", e)
        await close_online_runtime()
        await close_db()
        logger.info("Worker runner stopped cleanly")


def main() -> None:
    """入口函数：配置日志、信号处理、启动事件循环。"""
    from sales_agent.core.config import get_settings
    settings = get_settings()
    _log_level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=_log_level,
        format="%(asctime)s %(name)s %(levelname)s  %(message)s",
    )

    logger.info("Starting background worker runner...")

    loop = asyncio.new_event_loop()
    main_task = loop.create_task(run())

    # 信号处理：优雅关闭
    def _shutdown():
        if not main_task.done():
            main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    try:
        loop.run_until_complete(main_task)
    except KeyboardInterrupt:
        if not main_task.done():
            main_task.cancel()
            loop.run_until_complete(main_task)
    finally:
        loop.close()
