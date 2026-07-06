#!/bin/bash
# ============================================================
# Sales Agent — Docker Entrypoint
# ============================================================
# 根据 PROCESS_ROLE 环境变量选择启动命令：
#   all    → uvicorn (默认，兼容现有部署)
#   api    → sales-agent serve (仅 HTTP API)
#   stream → sales-agent stream (仅钉钉 Stream 长连接)
#   worker → sales-agent worker (仅后台任务)
# ============================================================

set -e

ROLE="${PROCESS_ROLE:-all}"
PORT="${PORT:-8000}"

echo "Starting sales-agent with PROCESS_ROLE=${ROLE}"

case "${ROLE}" in
  api)
    # init_db() 已处理 schema 创建 + 自动 migration（幂等，失败不阻断）。
    # 此处保留手动 migration 入口以兼容运维场景（RUN_MIGRATIONS=1），
    # 但失败不再 crash 整个容器 — init_db() 是主路径。
    if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
      echo "Running alembic upgrade head..."
      alembic upgrade head || echo "WARNING: alembic upgrade failed (non-fatal, init_db handles it)"
    fi
    exec sales-agent serve --host 0.0.0.0 --port "${PORT}"
    ;;
  stream)
    exec sales-agent stream
    ;;
  worker)
    exec sales-agent worker
    ;;
  all)
    exec uvicorn sales_agent.main:app --host 0.0.0.0 --port "${PORT}"
    ;;
  *)
    echo "ERROR: Unknown PROCESS_ROLE='${ROLE}'. Must be one of: all, api, stream, worker" >&2
    exit 1
    ;;
esac
