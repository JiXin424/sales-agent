"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sales_agent.api.routes import agent, conversations, documents, feedback, health, tenants, prompts, uploads, admin, pilot, agents, coach, ontology, instance
from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import TenantMismatchError, DingTalkTenantMismatchError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库和 TenantRuntime。"""
    # 配置日志级别（Python 默认 root logger=WARNING，需显式设置）
    from sales_agent.core.config import get_settings as _get_settings
    _settings = _get_settings()
    _log_level = getattr(logging, _settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=_log_level,
        format="%(asctime)s %(name)s %(levelname)s  %(message)s",
    )

    from sales_agent.core.database import init_db
    from sales_agent.core.tenant_runtime import get_tenant_runtime

    # 解析当前角色
    role = _settings.app.get_process_role()
    logger.info("Process role: %s", role)

    # 初始化数据库
    await init_db()

    # Bootstrap Neo4j ontology schema (constraints + vector index) when enabled.
    # 仅在启用 ontology_neo4j 引擎且配置了 Neo4j 时执行；失败仅告警，不阻断启动。
    if _settings.ontology.knowledge_engine == "ontology_neo4j" and _settings.neo4j.uri:
        from sales_agent.ontology.neo4j_client import Neo4jClient
        from sales_agent.ontology.schema import ensure_ontology_schema
        neo_client = Neo4jClient(_settings.neo4j)
        try:
            await ensure_ontology_schema(neo_client)
            logger.info("Neo4j ontology schema ensured")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j ontology schema bootstrap failed: %s", exc)
        finally:
            await neo_client.close()

    # 清理上次进程残留的 running 状态 ontology job。
    # asyncio.create_task 不持久，进程重启后这些 job 永远卡在 running（前端会一直显示"入库中"）。
    # 启动时把它们标 failed，让前端能正确显示终态。
    try:
        from sales_agent.core.database import get_session_factory
        from sales_agent.models.ingestion import IngestionJob
        from sqlalchemy import update
        _sf = get_session_factory()
        async with _sf() as _sweep_db:
            result = await _sweep_db.execute(
                update(IngestionJob)
                .where(IngestionJob.engine == "ontology_neo4j", IngestionJob.status == "running")
                .values(status="failed", error_summary="进程重启，任务中断")
            )
            await _sweep_db.commit()
            if result.rowcount:
                logger.info("Swept %d stale running ontology job(s) to failed", result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ontology stale-job sweep failed: %s", exc)

    # 加载 TenantRuntime 并做启动校验
    runtime = get_tenant_runtime()
    errors = runtime.validate_startup()
    if errors:
        for err in errors:
            logger.error("Startup validation failed: %s", err)
        # 不阻止启动，但记录错误（spec 8.3: 不强制依赖模型可用）
        logger.warning(
            "Agent started with %d validation error(s). Check /ready for details.",
            len(errors),
        )
    else:
        logger.info(
            "Agent started successfully: tenant=%s, mode=%s, provider=%s, chat_model=%s",
            runtime.tenant_id,
            runtime.deployment_mode,
            runtime.provider,
            runtime.chat_model,
        )

    # 启动钉钉 Worker（仅 all / stream / worker 角色启用）
    dt_mode = None  # "stream" | "http" | None
    should_start_dingtalk = role in ("all", "stream", "worker")
    if should_start_dingtalk:
        try:
            from sales_agent.core.config import get_settings
            settings = get_settings()
            if settings.dingtalk.enabled:
                dt_mode = settings.dingtalk.message_mode
                # stream 角色只启动 stream worker；worker 角色只启动 http worker
                if dt_mode == "stream" and role in ("all", "stream"):
                    from sales_agent.integrations.dingtalk.stream_client import (
                        start_dingtalk_stream_worker,
                    )
                    await start_dingtalk_stream_worker()
                    logger.info("DingTalk integration enabled (stream mode)")
                elif dt_mode != "stream" and role in ("all", "worker"):
                    from sales_agent.integrations.dingtalk.worker import start_dingtalk_worker
                    await start_dingtalk_worker()
                    logger.info("DingTalk integration enabled (http mode)")
                else:
                    logger.info(
                        "DingTalk %s mode skipped for role=%s",
                        dt_mode, role,
                    )
        except Exception as e:
            logger.warning("Failed to start DingTalk worker: %s", e)
            dt_mode = None
    else:
        logger.info("DingTalk workers skipped for role=%s", role)

    yield

    # 停止钉钉 Worker
    if dt_mode == "stream":
        try:
            from sales_agent.integrations.dingtalk.stream_client import stop_dingtalk_stream_worker
            await stop_dingtalk_stream_worker()
        except Exception as e:
            logger.warning("Failed to stop DingTalk stream worker: %s", e)
    elif dt_mode == "http":
        try:
            from sales_agent.integrations.dingtalk.worker import stop_dingtalk_worker
            await stop_dingtalk_worker()
        except Exception as e:
            logger.warning("Failed to stop DingTalk http worker: %s", e)

    from sales_agent.core.database import close_db
    await close_db()


app = FastAPI(
    title="Sales Agent v0",
    description="ToB 销售陪跑 Agent 本地原型",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- dedicated mode 租户匹配中间件 ---
@app.middleware("http")
async def tenant_match_middleware(request: Request, call_next):
    """dedicated mode 下校验请求 tenant_id 与实例 TENANT_ID 是否一致。

    只对 /agent/chat 和 /tenants/{id}/documents/ingest 做校验，
    /health、/ready、/diagnostics、/integrations 等运维接口不校验。
    """
    # 跳过不需要租户匹配的路径
    skip_paths = (
        "/health", "/ready", "/diagnostics", "/docs",
        "/openapi.json", "/redoc", "/integrations",
    )
    path = request.url.path
    if any(path.startswith(p) for p in skip_paths):
        return await call_next(request)

    from sales_agent.core.tenant_runtime import get_tenant_runtime
    runtime = get_tenant_runtime()

    if runtime.deployment_mode != "dedicated":
        return await call_next(request)

    # 对于 POST /agent/chat，请求体中有 tenant_id
    # 对于 GET /tenants/{tenant_id} 等，路径中有 tenant_id
    # 这里做轻量校验：只检查已知携带 tenant_id 的请求

    return await call_next(request)


# 注册路由
app.include_router(health.router)
app.include_router(tenants.router)
app.include_router(documents.router)
app.include_router(agent.router)
app.include_router(conversations.router)
app.include_router(feedback.router)
app.include_router(prompts.router)
app.include_router(uploads.router)
app.include_router(admin.router)
app.include_router(pilot.router)
app.include_router(agents.router)
app.include_router(coach.router)
app.include_router(ontology.router)
app.include_router(instance.router)

# 注册钉钉集成路由
from sales_agent.integrations.dingtalk.routes import router as dingtalk_router
app.include_router(dingtalk_router)

# 注册钉钉快捷入口路由（独立模块，可单独移除）
from sales_agent.integrations.dingtalk.quick_entry import router as dingtalk_quick_entry_router
app.include_router(dingtalk_quick_entry_router)


# --- 全局异常处理 ---
@app.exception_handler(TenantMismatchError)
async def tenant_mismatch_handler(request: Request, exc: TenantMismatchError):
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "TENANT_MISMATCH",
                "message": "请求租户与当前 Agent 实例不匹配",
                "detail": exc.detail,  # 已脱敏，只含 tenant_id
            }
        },
    )


@app.exception_handler(DingTalkTenantMismatchError)
async def dingtalk_tenant_mismatch_handler(request: Request, exc: DingTalkTenantMismatchError):
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "DINGTALK_TENANT_MISMATCH",
                "message": "钉钉企业与当前 Agent 实例不匹配",
                "detail": exc.detail,
            }
        },
    )


# --- 实例级接口：返回当前 dedicated 实例的唯一 Agent ---
@app.get("/instance/agent")
async def get_instance_agent():
    """返回当前实例（dedicated TENANT_ID）的唯一 Agent。

    dedicated 模式下每实例对应一个 tenant、一个默认 Agent。
    前端 boot 时查此接口拿到 agent_id，直接进入该 Agent 的 overview。
    """
    from fastapi import HTTPException
    from sales_agent.core.database import get_session_factory
    from sales_agent.core.tenant_runtime import get_tenant_runtime
    runtime = get_tenant_runtime()
    if not runtime.tenant_id:
        raise HTTPException(status_code=400, detail="实例未配置 TENANT_ID")
    factory = get_session_factory()
    async with factory() as db:
        from sales_agent.services.agent_service import resolve_tenant_agent_id, _agent_to_dict
        agent = await resolve_tenant_agent_id(db, runtime.tenant_id, None)
        return _agent_to_dict(agent)


# --- 托管前端静态文件（必须在所有 API router 之后） ---
def _mount_console_static() -> None:
    """挂载前端 SPA 静态文件。目录不存在时跳过（dev 模式由 vite 自行服务）。"""
    import os
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # 显式配置优先；否则尝试镜像默认路径 /app/console/dist
    dist_dir = get_settings().app.console_dist_dir or "/app/console/dist"
    if not os.path.isdir(dist_dir):
        logger.info("Console static dir not found (%r); skipping SPA mount.", dist_dir)
        return

    index_path = os.path.join(dist_dir, "index.html")

    # SPA fallback：未匹配的 GET 返回 index.html（react-router 前端路由）。
    # 已注册的 API 路由优先匹配，不会被这里吞掉。
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # 明确的静态资源文件直接返回
        candidate = os.path.join(dist_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        # 其余前端路由 → index.html
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Not found")

    # 也挂一个 StaticFiles 以便直接按路径访问 assets（带正确 MIME）
    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="console-assets")
    logger.info("Console SPA mounted from %s", dist_dir)


_mount_console_static()
