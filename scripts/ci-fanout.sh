#!/usr/bin/env bash
# CI fan-out:读 deploy/deploy-targets.json,按每台的 method 部署。
# 用法: IMAGE=registry.internal:5000/sales-agent:<sha> scripts/ci-fanout.sh
#
# 支持的 method:
#   deploy-release  标准部署(有源码,git sync → deploy-release.sh)
#   image-retag     镜像更新(有源码,保留现有 compose)
#   self-deploy     自部署(无源码,仅 docker login+pull+compose up -d)
#   image-deploy    无源码:docker run sales-agent-deploy 镜像(内含 compose + deploy-remote.sh)
#
# 可选字段:
#   has_source      bool,默认 true。false 时跳过 git sync
#   compose_file    仅 self-deploy 使用,默认 docker-compose.yml
#   env             仅 image-deploy 使用,指定用哪份 compose-<env>.yml(如 test)
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
        t.get("env", ""),
    ]))
PY

while IFS='|' read -r user host port dir method local name has_source compose_file env; do
  [ -z "$host" ] && continue

  case "$method" in
    deploy-release) script="${dir}/scripts/deploy-release.sh"; args="--yes" ;;
    image-retag)    script="${dir}/scripts/deploy-image-retag.sh"; args="--yes" ;;
    self-deploy)    script=""; args="" ;;  # 不需要脚本,直接用 docker 命令
    image-deploy)   script=""; args="" ;;  # 无源码:docker run deploy 镜像
    *) echo "未知 method: $method ($name),跳过" >&2; continue ;;
  esac

  echo "=== [$name] $method @ ${user}@${host}:${dir} ==="

  if [ "$local" = "True" ]; then
    # ── 本地执行 ──
    if [ "$method" = "image-deploy" ]; then
      # 本地 image-deploy(prod3 runner 本机):
      # 用 deploy 镜像(内含 compose-<env>.yml + deploy-remote.sh)在本机起容器。
      # 关键:挂 ${dir}(/root/code/sales-agent,持久克隆,含 secrets)而非 ${REPO_DIR}(ephemeral checkout)。
      DEPLOY_IMG="${REGISTRY_HOST:-registry.internal:5000}/sales-agent-deploy:${IMAGE##*:}"
      echo "[image-deploy] ${DEPLOY_IMG} env=${env:-?}"

      # 本地同步自包含运维脚本(stop-tenant.sh / check-tenant.sh / run-eval.sh),
      # 失败不阻断部署(与远程 image-deploy 同款兜底)。
      mkdir -p "${dir}/scripts"
      cp -f "${REPO_DIR}/scripts/stop-tenant.sh" "${REPO_DIR}/scripts/check-tenant.sh" \
            "${REPO_DIR}/scripts/run-eval.sh" "${dir}/scripts/" 2>/dev/null || true
      chmod +x "${dir}/scripts/"*.sh 2>/dev/null || true

      docker pull "${DEPLOY_IMG}" \
        && docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
             -v /root/.docker:/root/.docker:ro -v "${dir}:/workspace" \
             -e APP_IMAGE="${IMAGE}" -e FRONTEND_IMAGE="${FRONTEND_IMAGE:-}" \
             "${DEPLOY_IMG}" "${env}" \
        || echo "⚠️  [$name] local image-deploy 失败，继续下一台" >&2

    elif [ "$has_source" = "True" ] && [ "$method" != "self-deploy" ]; then
      git -C "$REPO_DIR" stash 2>/dev/null
      git -C "$REPO_DIR" fetch origin main && git -C "$REPO_DIR" reset --hard origin/main \
        && echo "[fanout] git synced to $(git -C "$REPO_DIR" rev-parse --short HEAD)"
      REGISTRY_IMAGE="$IMAGE" FRONTEND_IMAGE="${FRONTEND_IMAGE:-}" bash "$script" $args || echo "⚠️  [$name] 部署失败，继续下一台" >&2
    fi

  else
    # ── 远程执行 ──
    if [ "$method" = "self-deploy" ]; then
      # 无源码目标:仅 docker login + pull + compose up -d
      REG_HOST="${REGISTRY_HOST:-registry.internal:5000}"
      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "echo '[self-deploy] pulling ${IMAGE}'; docker pull '${IMAGE}' && docker tag '${IMAGE}' sales-agent:latest && docker compose -f '${dir}/${compose_file}' up -d && echo '[self-deploy] done'" \
        || echo "⚠️  [$name] self-deploy 失败，继续下一台" >&2
    elif [ "$method" = "image-deploy" ]; then
      # 无源码目标:docker run deploy 镜像(内含 compose + deploy-remote.sh)
      DEPLOY_IMG="${REGISTRY_HOST:-registry.internal:5000}/sales-agent-deploy:${IMAGE##*:}"
      echo "[image-deploy] ${DEPLOY_IMG} env=${env:-?}"

      # 同步自包含运维脚本到无源码目标机(stop-tenant.sh / check-tenant.sh),
      # 让目标机可直接 bash 停服务 / 验证健康。只传镜像不传脚本的链路补上这一步。
      # 失败不阻断后续部署(与下方 image-deploy 同款兜底)。
      # 注意 ssh 不带 -n:tar 流要经 stdin 传到远端解包;且此处 stdin 已被 tar 管道
      # 占用,不会读走外层 while 循环的 /tmp/ci-targets.txt(与其它分支的 ssh -n 不同)。
      tar -C "${REPO_DIR}/scripts" -cf - stop-tenant.sh check-tenant.sh run-eval.sh \
        | ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
          "mkdir -p '${dir}/scripts' && tar -C '${dir}/scripts' -xf - && chmod +x '${dir}/scripts/stop-tenant.sh' '${dir}/scripts/check-tenant.sh' '${dir}/scripts/run-eval.sh'" \
        || echo "⚠️  [$name] 运维脚本同步失败，继续部署" >&2

      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "docker pull '${DEPLOY_IMG}' && docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /root/.docker:/root/.docker:ro -v '${dir}:/workspace' -e APP_IMAGE='${IMAGE}' -e FRONTEND_IMAGE='${FRONTEND_IMAGE:-}' '${DEPLOY_IMG}' '${env}'" \
        || echo "⚠️  [$name] image-deploy 失败，继续下一台" >&2
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
