#!/usr/bin/env bash
# 把代码同步到"源码镜像"服务器(未来加的开发机/调试机)。
# 读 deploy/code-sync-targets.json,对每台 SSH git pull。清单为空则 no-op。
# 注意:每台源码服务器需先 git clone 主控 Gitea 仓库(并配好 pull 认证),本脚本只负责 pull。
set -euo pipefail
TARGETS_FILE="${TARGETS_FILE:-deploy/code-sync-targets.json}"
[ -f "$TARGETS_FILE" ] || { echo "无 $TARGETS_FILE,跳过 code 同步"; exit 0; }

mapfile -t lines < <(python3 - "$TARGETS_FILE" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for t in d.get("targets",[]):
    print("|".join([t.get("user","root"),t["host"],str(t.get("port",22)),t["dir"]]))
PY
)
if [ "${#lines[@]}" -eq 0 ] || [ -z "${lines[0]}" ]; then
  echo "code-sync 清单为空,跳过"
  exit 0
fi
for line in "${lines[@]}"; do
  IFS='|' read -r user host port dir <<< "$line"
  echo "=== git pull @ ${user}@${host}:${dir} ==="
  ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
    "cd '${dir}' && git fetch origin && git reset --hard origin/main && echo synced-to-\$(git rev-parse --short HEAD)"
done
echo "code-sync 完成"
