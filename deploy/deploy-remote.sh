#!/usr/bin/env bash
# 在无源码目标机上执行（由 deploy 镜像 docker run 触发，挂 docker.sock + secrets）。
# 用法（容器内 entrypoint）：deploy-remote.sh <env>   例：deploy-remote.sh hangzhou
set -euo pipefail

ENV="${1:?用法: deploy-remote.sh <env>}"
COMPOSE="/deploy/compose-${ENV}.yml"
APP_IMAGE="${APP_IMAGE:?需要 APP_IMAGE=registry.internal:5000/sales-agent:<sha>}"
COMPOSE_FILE_ENV="${COMPOSE_FILE_ENV:-/secrets/neo4j.env}"

[ -f "$COMPOSE" ] || { echo "ERROR: $COMPOSE 不在 deploy 镜像里（该 env 未 render?）" >&2; exit 1; }
[ -f "$COMPOSE_FILE_ENV" ] || { echo "ERROR: $COMPOSE_FILE_ENV 未挂载（secrets/neo4j.env 缺失?）" >&2; exit 1; }

echo "[deploy-remote] env=${ENV} app=${APP_IMAGE}"

# 1. pull app 镜像，retag 成 compose 引用的本地 tag sales-agent:latest
docker pull "$APP_IMAGE"
docker tag "$APP_IMAGE" sales-agent:latest

# 2. up（neo4j 密码经 --env-file 插值 ${NEO4J_PASSWORD}）
docker compose -f "$COMPOSE" --env-file "$COMPOSE_FILE_ENV" up -d

# 3. 等 api 容器 running（compose 里 api 无 healthcheck 字段，只校验 running；
#    业务健康靠 app /health + /ready，由人/ci-fanout 尾部复查）
echo "[deploy-remote] 等待 api running..."
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" ps --status=running 2>/dev/null | grep -q -- "-api"; then
    echo "[deploy-remote] ok (after ${i}x2s)"
    exit 0
  fi
  sleep 2
done
echo "⚠️ [deploy-remote] api 未就绪，查 docker compose -f $COMPOSE logs" >&2
exit 1
