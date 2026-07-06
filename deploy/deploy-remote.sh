#!/usr/bin/env bash
# 在无源码目标机上执行（由 deploy 镜像 docker run 触发）。
# 用法（容器内 entrypoint）：deploy-remote.sh <env>   例：deploy-remote.sh test
#
# 挂载约定（ci-fanout image-deploy 负责）：
#   /var/run/docker.sock        宿主 docker（拉镜像 + compose up）
#   /root/.docker               docker 认证（pull registry.internal）
#   /workspace                  目标机的代码目录（含 secrets/ data/ logs/，无源码目标机瘦身后只留这些）
set -euo pipefail

ENV="${1:?用法: deploy-remote.sh <env>}"
COMPOSE="/deploy/compose-${ENV}.yml"
APP_IMAGE="${APP_IMAGE:?需要 APP_IMAGE=registry.internal:5000/sales-agent:<sha>}"
WORKSPACE="${WORKSPACE:-/workspace}"
PROJECT="${PROJECT:-sales-agent}"

[ -f "$COMPOSE" ] || { echo "ERROR: $COMPOSE 不在 deploy 镜像里（该 env 未 render?）" >&2; exit 1; }
[ -f "$WORKSPACE/secrets/neo4j.env" ] || { echo "ERROR: $WORKSPACE/secrets/neo4j.env 未挂载（目标机 secrets 缺?）" >&2; exit 1; }

echo "[deploy-remote] env=${ENV} app=${APP_IMAGE} workspace=${WORKSPACE} project=${PROJECT}"

# 1. pull app 镜像 + frontend 镜像，retag 成 compose 引用的本地 tag(compose 里用 sales-agent:latest / sales-agent-frontend:latest)
docker pull "$APP_IMAGE"
docker tag "$APP_IMAGE" sales-agent:latest
if [ -n "${FRONTEND_IMAGE:-}" ]; then
  docker pull "$FRONTEND_IMAGE"
  docker tag "$FRONTEND_IMAGE" sales-agent-frontend:latest
fi

# 2. up：--project-directory 指向 workspace（compose 相对路径 ./data ./logs ./secrets 落在目标机数据上）；
#    -p 匹配现有 project 名（复用现有容器/volume/network，避免重名冲突）；
#    --env-file 注入 neo4j 密码（${NEO4J_PASSWORD} 插值）。
docker compose -f "$COMPOSE" --project-directory "$WORKSPACE" -p "$PROJECT" \
  --env-file "$WORKSPACE/secrets/neo4j.env" up -d

# 3. 等 api running + /health 200（init_db 完成），再校验 DB schema 一致性。
#    注意：旧版只 grep '-api' running 就 exit 0 —— 但容器 running ≠ app 就绪，
#    更 ≠ stream 就绪。这里用 /health 200 作为 init_db 完成的可靠信号
#    （FastAPI lifespan startup 同步 await init_db），并补上 schema 校验，
#    捕获 stamp-head 兜底导致的幽灵漂移（alembic_version=head 但列未落地）。
echo "[deploy-remote] 等待 api running..."
API_CT=""
for i in $(seq 1 30); do
  API_CT=$(docker compose -f "$COMPOSE" -p "$PROJECT" ps --format '{{.Name}}' 2>/dev/null \
           | grep -- '-api$' | head -1 || true)
  [ -n "$API_CT" ] && break
  sleep 2
done
if [ -z "$API_CT" ]; then
  echo "⚠️ [deploy-remote] api 容器未 running，查 docker compose -f $COMPOSE -p $PROJECT logs" >&2
  exit 1
fi

# 等 /health 200 = lifespan startup 完成 = init_db() 已跑完（成功或 stamp-head 兜底都算完成）
for i in $(seq 1 30); do
  if docker exec "$API_CT" python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=2).status==200 else 1)" \
    2>/dev/null; then
    echo "[deploy-remote] api /health 就绪 (after ${i}x2s)"
    break
  fi
  [ "$i" = "30" ] && {
    echo "⚠️ [deploy-remote] api /health 60s 内未就绪，查 docker compose -f $COMPOSE -p $PROJECT logs" >&2
    exit 1
  }
  sleep 2
done

# 校验 DB schema：校验脚本在 app 镜像 /app/scripts/，从容器 env 读 DATABASE_URL。
# 失败 = 幽灵漂移（model 有列、DB 没有），部署必须中止，否则 stream 会 crash。
echo "[deploy-remote] 校验 DB schema 一致性..."
docker exec "$API_CT" python /app/scripts/check_schema_consistency.py \
  || { echo "❌ [deploy-remote] DB schema 校验失败 — 幽灵漂移，有 migration DDL 未落地，部署中止" >&2
       echo "   查 docker compose -f $COMPOSE -p $PROJECT logs，用 backfill migration 补齐缺失列" >&2
       exit 1; }
echo "[deploy-remote] ✅ schema 校验通过"
exit 0
