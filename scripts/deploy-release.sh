#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INVENTORY="${INVENTORY:-deploy/tenants.json}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.generated.yml}"
CONFIRM_WORD="DEPLOY"

usage() {
  cat <<USAGE
Usage: scripts/deploy-release.sh [--yes]

Deploy a Sales Agent release bundle on this server.

Environment overrides:
  INVENTORY     Tenant inventory path. Default: deploy/tenants.json
  COMPOSE_FILE  Generated compose path. Default: docker-compose.generated.yml
USAGE
}

ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: missing required command: $1" >&2
    exit 1
  fi
}

need_cmd docker
need_cmd python3
need_cmd curl
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin is required" >&2
  exit 1
fi

mkdir -p deploy secrets data logs traefik/certs images

if [ ! -f "$INVENTORY" ]; then
  if [ ! -f deploy/tenants.example.json ]; then
    echo "ERROR: $INVENTORY missing and deploy/tenants.example.json not found" >&2
    exit 1
  fi
  cp deploy/tenants.example.json "$INVENTORY"
  echo "Created $INVENTORY from deploy/tenants.example.json"
fi

python3 - "$INVENTORY" <<'PY'
import json
import pathlib
import sys

inventory = pathlib.Path(sys.argv[1])
data = json.loads(inventory.read_text(encoding="utf-8"))
template = pathlib.Path("deploy/tenant.env.example")
if not template.exists():
    raise SystemExit("deploy/tenant.env.example is missing")
for tenant in data.get("tenants", []):
    env_file = pathlib.Path(tenant.get("env_file", ""))
    if not env_file:
        raise SystemExit(f"tenant {tenant.get('id')}: env_file is required")
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        text = template.read_text(encoding="utf-8")
        tenant_id = tenant.get("id", "")
        tenant_name = tenant.get("name", tenant_id)
        domain = tenant.get("domain", f"{tenant_id}.example.com")
        text = text.replace("TENANT_ID=acme", f"TENANT_ID={tenant_id}")
        text = text.replace("TENANT_NAME=ACME Enterprise", f"TENANT_NAME={tenant_name}")
        text = text.replace("VECTOR_COLLECTION=acme", f"VECTOR_COLLECTION={tenant_id}")
        text = text.replace("DATA_DIR=/data/acme", f"DATA_DIR=/data/{tenant_id}")
        text = text.replace("LOG_DIR=/logs/acme", f"LOG_DIR=/logs/{tenant_id}")
        text = text.replace("DINGTALK_PUBLIC_URL=https://acme-agent.example.com", f"DINGTALK_PUBLIC_URL=https://{domain}")
        env_file.write_text(text, encoding="utf-8")
        env_file.chmod(0o600)
        print(f"created env template: {env_file}")
PY

echo
echo "Before deployment, confirm these files contain the latest production settings:"
python3 - "$INVENTORY" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"  inventory: {pathlib.Path(sys.argv[1]).resolve()}")
for tenant in data.get("tenants", []):
    print(f"  tenant env: {pathlib.Path(tenant['env_file']).resolve()}")
print("  generated compose will be written after confirmation")
PY

echo
python3 - "$INVENTORY" <<'PY'
import json
import pathlib
import re
import sys

PLACEHOLDERS = (
    "sk-example",
    "api.example.com",
    "ding-corp-id",
    "ding-app-key",
    "ding-app-secret",
    "ding-robot-code",
    "acme-agent.example.com",
    "beta-agent.example.com",
)
REQUIRED_KEYS = (
    "TENANT_ID",
    "TENANT_NAME",
    "MODEL_API_KEY",
    "MODEL_BASE_URL",
    "MODEL_CHAT_MODEL",
    "MODEL_EMBEDDING_MODEL",
    "DINGTALK_ENABLED",
    "DINGTALK_MESSAGE_MODE",
    "VECTOR_COLLECTION",
    "DATA_DIR",
    "LOG_DIR",
)
DINGTALK_KEYS = (
    "DINGTALK_CORP_ID",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_ROBOT_CODE",
)
QUICK_ENTRY_KEYS = (
    "DINGTALK_PUBLIC_URL",
)

def parse_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values

errors = []
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for tenant in data.get("tenants", []):
    tenant_id = tenant.get("id", "<missing>")
    env_path = pathlib.Path(tenant.get("env_file", ""))
    if not env_path.exists():
        errors.append(f"{tenant_id}: env file missing: {env_path}")
        continue
    values = parse_env(env_path)
    for key in REQUIRED_KEYS:
        if not values.get(key):
            errors.append(f"{tenant_id}: {env_path} missing {key}")
    dingtalk_enabled = values.get("DINGTALK_ENABLED", "").lower() in {"true", "1", "yes"}
    register_quick_entry = values.get("DINGTALK_REGISTER_QUICK_ENTRY", "true").lower() not in {"false", "0", "no"}
    if dingtalk_enabled:
        for key in DINGTALK_KEYS:
            if not values.get(key):
                errors.append(f"{tenant_id}: {env_path} missing {key}")
        if register_quick_entry:
            for key in QUICK_ENTRY_KEYS:
                if not values.get(key):
                    errors.append(f"{tenant_id}: {env_path} missing {key} for quick entry registration")
    if values.get("TENANT_ID") and values.get("TENANT_ID") != tenant_id:
        errors.append(f"{tenant_id}: TENANT_ID in {env_path} is {values.get('TENANT_ID')!r}")
    joined = env_path.read_text(encoding="utf-8")
    for marker in PLACEHOLDERS:
        if marker in joined:
            errors.append(f"{tenant_id}: replace placeholder {marker!r} in {env_path}")
    domain = tenant.get("domain", "")
    if domain and "example.com" in domain:
        errors.append(f"{tenant_id}: replace example domain in {pathlib.Path(sys.argv[1])}")
    if not isinstance(tenant.get("api_port"), int):
        errors.append(f"{tenant_id}: api_port must be an integer in {pathlib.Path(sys.argv[1])}")

if errors:
    print("Configuration is not ready:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    raise SystemExit(1)
print("Configuration placeholder check passed.")
PY

if [ "$ASSUME_YES" -ne 1 ]; then
  echo
  echo "Type $CONFIRM_WORD to confirm the files above are the latest credentials/config for this server."
  read -r answer
  if [ "$answer" != "$CONFIRM_WORD" ]; then
    echo "Aborted. Edit the config files above, then rerun scripts/deploy-release.sh."
    exit 1
  fi
fi

if compgen -G "images/*.tar" >/dev/null; then
  for image_tar in images/*.tar; do
    echo "Loading docker image: $image_tar"
    docker load -i "$image_tar"
  done
else
  echo "No image tar found under images/. Docker will use local/pullable image tags from $INVENTORY."
fi

python3 scripts/render-multitenant-deploy.py "$INVENTORY" --compose-out "$COMPOSE_FILE"

echo "Starting services with $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" up -d

echo "Running tenant checks"
scripts/check-all-tenants.sh "$INVENTORY"

echo "Registering DingTalk quick entries when enabled"
python3 - "$INVENTORY" <<'PY' | while IFS=$'\t' read -r tenant_id register_url; do
import json
import pathlib
import urllib.parse
import sys


def parse_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('\"').strip("'")
    return values

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for tenant in data.get("tenants", []):
    env_path = pathlib.Path(tenant["env_file"])
    values = parse_env(env_path)
    if values.get("DINGTALK_ENABLED", "").lower() not in {"true", "1", "yes"}:
        print(f"{tenant['id']}\tSKIP_DISABLED")
        continue
    if values.get("DINGTALK_REGISTER_QUICK_ENTRY", "true").lower() in {"false", "0", "no"}:
        print(f"{tenant['id']}\tSKIP_CONFIG")
        continue
    params = {
        "tenant_id": tenant["id"],
        "clear_first": values.get("DINGTALK_QUICK_ENTRY_CLEAR_FIRST", "true"),
        "name": values.get("DINGTALK_QUICK_ENTRY_NAME", "教练模式"),
        "entries": values.get("DINGTALK_QUICK_ENTRY_ENTRIES", "coach,small_win_appreciation,sales_block_breakthrough"),
    }
    url = f"http://127.0.0.1:{tenant['api_port']}/integrations/dingtalk/plugins/register?{urllib.parse.urlencode(params)}"
    print(f"{tenant['id']}\t{url}")
PY
  case "$register_url" in
    SKIP_DISABLED) echo "skip: $tenant_id DingTalk disabled" ;;
    SKIP_CONFIG) echo "skip: $tenant_id quick entry registration disabled" ;;
    *)
      echo "register: $tenant_id DingTalk quick entries"
      curl -fsS -X POST "$register_url" >/dev/null
      echo "ok: $tenant_id DingTalk quick entries registered"
      ;;
  esac
done

echo "Deployment complete."
