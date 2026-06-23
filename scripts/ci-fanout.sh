#!/usr/bin/env bash
# CI fan-out:读 deploy/deploy-targets.json,按每台的 method 部署。
# 用法: IMAGE=registry.internal:5000/sales-agent:<sha> scripts/ci-fanout.sh
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
