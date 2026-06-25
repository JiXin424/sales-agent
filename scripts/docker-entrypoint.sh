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
    # 仅 api 角色跑 pg migration（幂等）；stream/worker 不跑，避免并发竞争。
    # RUN_MIGRATIONS=0 可跳过（调试用）。
    if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
      echo "Running alembic upgrade head..."
      alembic upgrade head || { echo "ERROR: alembic upgrade failed"; exit 1; }
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
