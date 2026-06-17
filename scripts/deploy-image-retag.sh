#!/usr/bin/env bash
# 生产机(本机)的 image-retag 部署。
# 保留现有 docker-compose.yml + traefik 路由(qiyelongxia.com.cn 不动),
# 只把镜像更新成 registry 里的新版本:pull → retag 成 compose 引用的 sales-agent:latest → up -d。
# 这是"最低风险"的生产更新方式,不动 compose/网络/traefik。
# 用法:REGISTRY_IMAGE=registry.internal:5000/sales-agent:<sha> scripts/deploy-image-retag.sh [--yes]
set -euo pipefail

REGISTRY_IMAGE="${REGISTRY_IMAGE:?需要 REGISTRY_IMAGE 环境变量,形如 registry.internal:5000/sales-agent:<sha>}"
API_PORT="${API_PORT:-8001}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROFILE="${PROFILE:-taishan-split}"

cd "$(dirname "$0")/.."

echo "Pulling ${REGISTRY_IMAGE}"
docker pull "${REGISTRY_IMAGE}"

echo "Retag → sales-agent:latest(compose 引用的本地 tag)"
docker tag "${REGISTRY_IMAGE}" sales-agent:latest

echo "Recreate containers with new image(compose/traefik 不变,profile=${PROFILE})"
docker compose -f "${COMPOSE_FILE}" --profile "${PROFILE}" up -d

echo "等待启动 + 健康检查(api :${API_PORT})"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    echo "health ok (after ${i}x2s)"
    exit 0
  fi
  sleep 2
done
echo "⚠️ health 未在 40s 内就绪,请检查 docker logs sales-agent-taishan-api" >&2
exit 1
