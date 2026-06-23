#!/usr/bin/env bash
# CI fan-out:读 deploy/deploy-targets.json,按每台的 method 部署。
# 用法: IMAGE=registry.internal:5000/sales-agent:<sha> scripts/ci-fanout.sh
#
# method:
#   deploy-release → scripts/deploy-release.sh --yes  (生成式 compose,用于全新/隔离部署)
#   image-retag    → scripts/deploy-image-retag.sh    (保留现有 compose+traefik,用于生产机)
# local=true 的目标在本机直接执行(不 SSH)。
set -euo pipefail
IMAGE="${IMAGE:?需要 IMAGE 环境变量}"

python3 - <<'PY' > /tmp/ci-targets.txt
import json
d = json.load(open("deploy/deploy-targets.json"))
for t in d.get("targets", []):
    print("|".join([
        t.get("user", "root"),
        t["host"],
        str(t.get("port", 22)),
        t["dir"],
        t.get("method", "deploy-release"),
        str(t.get("local", False)),
        t.get("name", t["host"]),
    ]))
PY

while IFS='|' read -r user host port dir method local name; do
  [ -z "$host" ] && continue
  case "$method" in
    deploy-release) script="${dir}/scripts/deploy-release.sh"; args="--yes" ;;
    image-retag)    script="${dir}/scripts/deploy-image-retag.sh"; args="--yes" ;;
    *) echo "未知 method: $method ($name),跳过" >&2; continue ;;
  esac
  echo "=== [$name] $method @ ${user}@${host}:${dir} ==="
  if [ "$local" = "True" ]; then
    ( REGISTRY_IMAGE="$IMAGE" bash "$script" $args )
  else
    ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
      "REGISTRY_IMAGE='${IMAGE}' bash '${script}' ${args}"
  fi
done < /tmp/ci-targets.txt
echo "fan-out 完成"
