#!/usr/bin/env bash
# 在无源码目标机上执行（由 deploy 镜像 docker run 触发）。
# 用法（容器内 entrypoint）：deploy-remote.sh <env>   例：deploy-remote.sh hangzhou
#
# 挂载约定（ci-fanout image-deploy 负责）：
#   /var/run/docker.sock        宿主 docker（拉镜像 + compose up）
#   /root/.docker               docker 认证（pull registry.internal）
#   /workspace                  目标机的代码目录（含 secrets/ data/ logs/，杭州瘦身后只留这些）
set -euo pipefail

ENV="${1:?用法: deploy-remote.sh <env>}"
COMPOSE="/deploy/compose-${ENV}.yml"
APP_IMAGE="${APP_IMAGE:?需要 APP_IMAGE=registry.internal:5000/sales-agent:<sha>}"
WORKSPACE="${WORKSPACE:-/workspace}"
PROJECT="${PROJECT:-sales-agent}"

[ -f "$COMPOSE" ] || { echo "ERROR: $COMPOSE 不在 deploy 镜像里（该 env 未 render?）" >&2; exit 1; }
[ -f "$WORKSPACE/secrets/neo4j.env" ] || { echo "ERROR: $WORKSPACE/secrets/neo4j.env 未挂载（目标机 secrets 缺?）" >&2; exit 1; }

echo "[deploy-remote] env=${ENV} app=${APP_IMAGE} workspace=${WORKSPACE} project=${PROJECT}"

# 1. pull app 镜像，retag 成 compose 引用的本地 tag sales-agent:latest
docker pull "$APP_IMAGE"
docker tag "$APP_IMAGE" sales-agent:latest

# 2. up：--project-directory 指向 workspace（compose 相对路径 ./data ./logs ./secrets 落在目标机数据上）；
#    -p 匹配现有 project 名（复用现有容器/volume/network，避免重名冲突）；
#    --env-file 注入 neo4j 密码（${NEO4J_PASSWORD} 插值）。
docker compose -f "$COMPOSE" --project-directory "$WORKSPACE" -p "$PROJECT" \
  --env-file "$WORKSPACE/secrets/neo4j.env" up -d

# 3. 等 api running（compose 里 api 无 healthcheck 字段；业务健康靠 app /ready，人/ci-fanout 尾部复查）
echo "[deploy-remote] 等待 api running..."
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" -p "$PROJECT" ps --status=running 2>/dev/null | grep -q -- "-api"; then
    echo "[deploy-remote] ok (after ${i}x2s)"
    exit 0
  fi
  sleep 2
done
echo "⚠️ [deploy-remote] api 未就绪，查 docker compose -f $COMPOSE -p $PROJECT logs" >&2
exit 1
