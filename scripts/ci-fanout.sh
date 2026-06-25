#!/usr/bin/env bash
# CI fan-out:读 deploy/deploy-targets.json,按每台的 method 部署。
# 用法: IMAGE=registry.internal:5000/sales-agent:<sha> scripts/ci-fanout.sh
#
# 支持的 method:
#   deploy-release  标准部署(有源码,git sync → deploy-release.sh)
#   image-retag     镜像更新(有源码,保留现有 compose)
#   self-deploy     自部署(无源码,仅 docker login+pull+compose up -d)
#
# 可选字段:
#   has_source      bool,默认 true。false 时跳过 git sync
#   compose_file    仅 self-deploy 使用,默认 docker-compose.yml
set -euo pipefail
IMAGE="${IMAGE:?需要 IMAGE 环境变量}"

# 仓库根目录 = 本脚本所在目录的父目录
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$REPO_DIR/deploy/deploy-targets.json" <<'PY' > /tmp/ci-targets.txt
import json, sys
d = json.load(open(sys.argv[1]))
for t in d.get("targets", []):
    print("|".join([
        t.get("user", "root"),
        t["host"],
        str(t.get("port", 22)),
        t.get("dir", ""),
        t.get("method", "deploy-release"),
        str(t.get("local", False)),
        t.get("name", t["host"]),
        str(t.get("has_source", True)),
        t.get("compose_file", "docker-compose.yml"),
    ]))
PY

while IFS='|' read -r user host port dir method local name has_source compose_file; do
  [ -z "$host" ] && continue

  case "$method" in
    deploy-release) script="${dir}/scripts/deploy-release.sh"; args="--yes" ;;
    image-retag)    script="${dir}/scripts/deploy-image-retag.sh"; args="--yes" ;;
    self-deploy)    script=""; args="" ;;  # 不需要脚本,直接用 docker 命令
    *) echo "未知 method: $method ($name),跳过" >&2; continue ;;
  esac

  echo "=== [$name] $method @ ${user}@${host}:${dir} ==="

  if [ "$local" = "True" ]; then
    # ── 本地执行 ──
    if [ "$has_source" = "True" ] && [ "$method" != "self-deploy" ]; then
      git -C "$REPO_DIR" stash 2>/dev/null
      git -C "$REPO_DIR" fetch origin main && git -C "$REPO_DIR" reset --hard origin/main \
        && echo "[fanout] git synced to $(git -C "$REPO_DIR" rev-parse --short HEAD)"
    fi
    REGISTRY_IMAGE="$IMAGE" FRONTEND_IMAGE="${FRONTEND_IMAGE:-}" bash "$script" $args || echo "⚠️  [$name] 部署失败，继续下一台" >&2

  else
    # ── 远程执行 ──
    if [ "$method" = "self-deploy" ]; then
      # 无源码目标:仅 docker login + pull + compose up -d
      REG_HOST="${REGISTRY_HOST:-registry.internal:5000}"
      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "echo '[self-deploy] pulling ${IMAGE}'; docker pull '${IMAGE}' && docker tag '${IMAGE}' sales-agent:latest && docker compose -f '${dir}/${compose_file}' up -d && echo '[self-deploy] done'" \
        || echo "⚠️  [$name] self-deploy 失败，继续下一台" >&2
    elif [ "$has_source" = "True" ]; then
      # 有源码目标:先 git sync 再执行部署脚本
      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "git -C '${dir}' stash 2>/dev/null; git -C '${dir}' fetch origin main && git -C '${dir}' reset --hard origin/main && echo '[fanout] git synced to' \$(git -C '${dir}' rev-parse --short HEAD); REGISTRY_IMAGE='${IMAGE}' FRONTEND_IMAGE='${FRONTEND_IMAGE:-}' bash '${script}' ${args}" \
        || echo "⚠️  [$name] SSH 部署失败，继续下一台" >&2
    else
      # 有部署脚本但无源码(罕见):直接跑脚本,不 git sync
      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "REGISTRY_IMAGE='${IMAGE}' FRONTEND_IMAGE='${FRONTEND_IMAGE:-}' bash '${script}' ${args}" \
        || echo "⚠️  [$name] SSH 部署失败，继续下一台" >&2
    fi
  fi
done < /tmp/ci-targets.txt
echo "fan-out 完成"
