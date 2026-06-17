"""DingTalk Stream 角色独立运行器。

以 PROCESS_ROLE=stream 运行时，仅启动 DingTalk Stream 长连接，
不对外暴露完整 HTTP API 端口。

用法:
    sales-agent stream
    # 或
    PROCESS_ROLE=stream uvicorn sales_agent.main:app
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

logger = logging.getLogger(__name__)


async def run() -> None:
    """Stream 角色主循环：初始化 → 启动 Stream Worker → 阻塞等待。"""
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, close_db
    from sales_agent.core.tenant_runtime import get_tenant_runtime

    settings = get_settings()
    config = settings.dingtalk

    # 初始化数据库
    await init_db()
    logger.info("Database initialized (stream runner)")

    # 加载 TenantRuntime
    runtime = get_tenant_runtime()
    errors = runtime.validate_startup()
    if errors:
        for err in errors:
            logger.error("Startup validation failed: %s", err)
        logger.warning(
            "Stream runner started with %d validation error(s).",
            len(errors),
        )
    else:
        logger.info(
            "Stream runner ready: tenant=%s, corp_id=%s",
            runtime.tenant_id,
            config.corp_id,
        )

    # 检查 DingTalk 配置
    if not config.enabled:
        logger.error("DingTalk not enabled — stream runner has nothing to do. Exiting.")
        await close_db()
        return

    if config.message_mode != "stream":
        logger.error(
            "DingTalk message_mode=%s (expected 'stream'). Exiting.",
            config.message_mode,
        )
        await close_db()
        return

    # 启动 Stream Worker
    from sales_agent.integrations.dingtalk.stream_client import (
        start_dingtalk_stream_worker,
        stop_dingtalk_stream_worker,
    )
    await start_dingtalk_stream_worker()
    logger.info("DingTalk Stream worker started (standalone runner)")

    # 阻塞等待直到被取消（SIGTERM / SIGINT）
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Stream runner shutting down...")
        await stop_dingtalk_stream_worker()
        await close_db()
        logger.info("Stream runner stopped cleanly")


def main() -> None:
    """入口函数：配置日志、信号处理、启动事件循环。"""
    # 配置日志
    from sales_agent.core.config import get_settings
    settings = get_settings()
    _log_level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=_log_level,
        format="%(asctime)s %(name)s %(levelname)s  %(message)s",
    )

    logger.info("Starting DingTalk Stream runner...")

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
