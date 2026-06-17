"""Health / Ready / Diagnostics 路由。

- /health: 只检查 Agent 进程存活
- /ready: 检查模型配置、向量库、文件目录
- /diagnostics/model: 手动触发模型连通性检查
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Agent 进程存活检查。不依赖模型服务可用。"""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check():
    """就绪检查：模型配置、向量库、数据目录。"""
    from sales_agent.core.tenant_runtime import get_tenant_runtime

    runtime = get_tenant_runtime()
    errors = runtime.validate_startup()

    if errors:
        return {
            "status": "not_ready",
            "errors": errors,
            "tenant_id": runtime.tenant_id,
            "deployment_mode": runtime.deployment_mode,
        }

    return {
        "status": "ready",
        "tenant_id": runtime.tenant_id,
        "deployment_mode": runtime.deployment_mode,
        "debug": runtime.get_debug_info(),
    }


@router.get("/diagnostics/model")
async def model_diagnostics():
    """模型连通性检查（管理员手动触发）。"""
    from sales_agent.core.tenant_runtime import get_tenant_runtime
    import time

    runtime = get_tenant_runtime()
    debug_info = runtime.get_debug_info()

    if runtime.model_provider is None:
        return {
            "status": "error",
            "message": "ModelProvider not initialized",
            "debug": debug_info,
        }

    # 尝试调用 chat 模型
    chat_result = {"status": "unknown", "latency_ms": None, "error": None}
    try:
        start = time.time()
        response = await runtime.model_provider.chat.generate(
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=10,
        )
        chat_result["status"] = "ok"
        chat_result["latency_ms"] = int((time.time() - start) * 1000)
    except Exception as e:
        chat_result["status"] = "failed"
        # 不暴露明文 key，只暴露错误类型
        error_type = type(e).__name__
        error_msg = str(e)
        # 脱敏：移除可能包含 key 的信息
        if "api_key" in error_msg.lower() or "sk-" in error_msg.lower():
            error_msg = "[redacted: contains sensitive info]"
        chat_result["error"] = f"{error_type}: {error_msg}"

    # 尝试调用 embedding 模型
    embedding_result = {"status": "unknown", "latency_ms": None, "error": None}
    try:
        start = time.time()
        vectors = await runtime.model_provider.embedding.embed(["ping"])
        embedding_result["status"] = "ok" if vectors else "empty"
        embedding_result["latency_ms"] = int((time.time() - start) * 1000)
        if vectors:
            embedding_result["dimensions"] = len(vectors[0])
    except Exception as e:
        embedding_result["status"] = "failed"
        error_type = type(e).__name__
        error_msg = str(e)
        if "api_key" in error_msg.lower() or "sk-" in error_msg.lower():
            error_msg = "[redacted: contains sensitive info]"
        embedding_result["error"] = f"{error_type}: {error_msg}"

    overall = "ok" if (
        chat_result["status"] == "ok" and embedding_result["status"] == "ok"
    ) else "degraded"

    return {
        "status": overall,
        "tenant_id": runtime.tenant_id,
        "chat": chat_result,
        "embedding": embedding_result,
        "debug": debug_info,
    }


@router.get("/health/latency-stats")
async def latency_stats():
    """延迟统计（p50/p90/p95），按路径分组。

    对应 spec §13 监控指标。
    """
    from sales_agent.services.latency_stats import get_latency_stats_collector

    collector = get_latency_stats_collector()
    return {
        "status": "ok",
        "stats": collector.get_summary(),
    }
