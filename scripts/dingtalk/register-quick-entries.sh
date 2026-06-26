#!/usr/bin/env bash
# 注册钉钉快捷入口按钮（独立脚本，可在任意目标机本地执行）。
#
# 读取 inventory，对每个已启用钉钉的租户，调用其【本地】api 容器的端点
#   POST /integrations/dingtalk/t/{tenant_id}/plugins/register
# 全量重注册钉钉快捷入口按钮（clear_first=true 先清空再覆盖；URL 用新 /t/{tenant}/ path，
# 供共享域名下 Traefik 按租户分流）。
#
# 用途：补 CI image-deploy 流程漏掉的按钮注册步骤（image-deploy 只更新镜像，不注册按钮）。
#       也在任意租户的钉钉入口按钮丢失/需刷新时手动跑。
#
# 用法（在目标机的 sales-agent 仓库根执行）：
#   bash scripts/dingtalk/register-quick-entries.sh                        # 默认 deploy/tenants.json
#   bash scripts/dingtalk/register-quick-entries.sh deploy/tenants.prod3.json
set -euo pipefail

# 自动定位仓库根：脚本常被 scp 到 /tmp 后远程执行，需先 cd 到仓库根，
# 才能解析 deploy/tenants*.json 与 secrets/*.env 的相对路径。
# （否则 ssh 远程命令必须手动 cd，极易漏——本段自动兜底。）
if [ ! -f "deploy/tenants.json" ] && [ -d "/root/code/sales-agent" ]; then
  cd /root/code/sales-agent
fi

INVENTORY="${1:-deploy/tenants.json}"
if [ ! -f "$INVENTORY" ]; then
  echo "[ERROR] inventory 未找到: $INVENTORY（请在 sales-agent 仓库根执行，或传入正确路径）" >&2
  exit 1
fi

echo "[INFO] 注册钉钉快捷入口按钮，inventory=$INVENTORY"

# python 读 inventory + 各租户 env，输出「tenant_id<TAB>register_url 或 SKIP_*」；
# bash while 循环对每个 url 调 curl 注册（POST）。分离是为了让 curl 失败不致命、逐租户报告。
python3 - "$INVENTORY" <<'PY' | while IFS=$'\t' read -r tenant_id register_url; do
import json
import pathlib
import urllib.parse
import urllib.request
import sys


def parse_env(path: pathlib.Path) -> dict:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for tenant in data.get("tenants", []):
    env_path = pathlib.Path(tenant["env_file"])
    if not env_path.exists():
        print(f"{tenant['id']}\tSKIP_NO_ENV")
        continue
    values = parse_env(env_path)
    if values.get("DINGTALK_ENABLED", "").lower() not in {"true", "1", "yes"}:
        print(f"{tenant['id']}\tSKIP_DISABLED")
        continue
    if values.get("DINGTALK_REGISTER_QUICK_ENTRY", "true").lower() in {"false", "0", "no"}:
        print(f"{tenant['id']}\tSKIP_CONFIG")
        continue
    # 探测 api 容器是否在跑（inventory 可能含未部署的租户，跳过它们，避免误报失败）
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{tenant['api_port']}/health", timeout=3
        ).read()
    except Exception:
        print(f"{tenant['id']}\tSKIP_NOT_RUNNING")
        continue
    params = {
        "clear_first": values.get("DINGTALK_QUICK_ENTRY_CLEAR_FIRST", "true"),
        "name": values.get("DINGTALK_QUICK_ENTRY_NAME", "教练模式"),
        "entries": values.get(
            "DINGTALK_QUICK_ENTRY_ENTRIES",
            "coach,small_win_appreciation,sales_block_breakthrough",
        ),
    }
    base = (
        f"http://127.0.0.1:{tenant['api_port']}"
        f"/integrations/dingtalk/t/{tenant['id']}/plugins/register"
    )
    print(f"{tenant['id']}\t{base}?{urllib.parse.urlencode(params)}")
PY
  case "$register_url" in
    SKIP_NO_ENV)   echo "  skip: $tenant_id  env_file 不存在" ;;
    SKIP_DISABLED) echo "  skip: $tenant_id  DingTalk 未启用 (DINGTALK_ENABLED!=true)" ;;
    SKIP_CONFIG)   echo "  skip: $tenant_id  注册被 DINGTALK_REGISTER_QUICK_ENTRY=false 关闭" ;;
    SKIP_NOT_RUNNING) echo "  skip: $tenant_id  api 容器未运行（端口无响应，未部署？）" ;;
    "")
      : ;;  # python 末尾换行
    *)
      echo "  register: $tenant_id"
      if curl -fsS --max-time 90 -X POST "$register_url" >/dev/null; then
        echo "  ✅ ok: $tenant_id 钉钉快捷入口已注册"
      else
        echo "  ❌ FAIL: $tenant_id 注册失败 (curl rc=$?)"
      fi
      ;;
  esac
done

echo "[DONE] 钉钉快捷入口注册流程结束"
