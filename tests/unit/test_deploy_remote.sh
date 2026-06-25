#!/usr/bin/env bash
# deploy-remote.sh 冒烟：语法 + 关键逻辑断言。
set -e
SCRIPT="$(cd "$(dirname "$0")/../.." && pwd)/deploy/deploy-remote.sh"

bash -n "$SCRIPT"

grep -q 'compose-${ENV}' "$SCRIPT" || { echo "缺 compose 选择"; exit 1; }
grep -q -- '--env-file "$WORKSPACE/secrets/neo4j.env"' "$SCRIPT" || { echo "缺 --env-file 注入"; exit 1; }
grep -q -- '--project-directory' "$SCRIPT" || { echo "缺 --project-directory"; exit 1; }
grep -q 'docker pull "$APP_IMAGE"' "$SCRIPT" || { echo "缺 app pull"; exit 1; }
grep -q 'docker tag "$APP_IMAGE" sales-agent:latest' "$SCRIPT" || { echo "缺 retag"; exit 1; }
# migration 不在这里跑（靠 app 镜像 entrypoint）
test "$(grep -c 'alembic' "$SCRIPT")" -eq 0
echo "deploy-remote.sh 冒烟 OK"
