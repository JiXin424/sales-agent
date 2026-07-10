#!/usr/bin/env bash
# 部署后 DB schema 一致性校验：等 api 就绪（init_db 完成）→ exec 校验脚本。
# 用法: post-deploy-schema-check.sh <compose-file>
#
# project 名由 compose 自动推断（与 `docker compose up` 时一致 —— 同 CWD / compose name 字段）。
#
# 捕获 init_db stamp-head 兜底导致的「幽灵漂移」：alembic_version 标到 head，
# 但 create_all 不 ALTER 已存在表、migration 的 add_column 又被 stamp 跳过，
# 于是 model 有的列 DB 里没有（典型：conversation_messages.topic_id）。
# 校验基准是 ORM Base.metadata（应用真正会查的列），校验脚本在 app 镜像
# /app/scripts/check_schema_consistency.py。
#
# 时机关键：/health 200 = FastAPI lifespan startup 完成 = init_db() 已跑完
#（init_db 在 lifespan 里同步 await）。先等 /health 再校验，schema 状态稳定。
#
# 注：image-deploy 链路（deploy-remote.sh）因 --project-directory 不同，必须带
# -p，故 inline 同款逻辑而非调本脚本；本脚本仅 deploy-release.sh（本机部署）用。
set -euo pipefail

COMPOSE="${1:?用法: post-deploy-schema-check.sh <compose-file>}"

# 1. 找一个 running 的 -api 容器（共享 DB，校验一次即覆盖所有租户）
API_CT=""
for i in $(seq 1 30); do
  API_CT=$(docker compose -f "$COMPOSE" ps --format '{{.Name}}' 2>/dev/null \
           | grep -- '-api$' | head -1 || true)
  [ -n "$API_CT" ] && break
  sleep 2
done
if [ -z "$API_CT" ]; then
  echo "⚠️ [schema-check] 无 -api 容器 running，跳过校验（交 health check 兜底）" >&2
  exit 0
fi

# 2. 等 /health 200（init_db 完成的可靠信号）
for i in $(seq 1 30); do
  if docker exec "$API_CT" python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=2).status==200 else 1)" \
    2>/dev/null; then
    echo "[schema-check] api 就绪 (after ${i}x2s): $API_CT"
    break
  fi
  [ "$i" = "30" ] && {
    echo "⚠️ [schema-check] api /health 60s 内未就绪，跳过校验（交 health check 兜底）" >&2
    exit 0
  }
  sleep 2
done

# 3. exec 校验脚本（app 镜像自带，从容器 env 读 DATABASE_URL）
echo "[schema-check] 校验 DB schema 一致性..."
docker exec "$API_CT" python /app/scripts/check_schema_consistency.py
