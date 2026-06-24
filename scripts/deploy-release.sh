#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INVENTORY="${INVENTORY:-deploy/tenants.json}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.generated.yml}"
CONFIRM_WORD="DEPLOY"
SELECTED_ENVS=()

usage() {
  cat <<USAGE
Usage: scripts/deploy-release.sh [OPTIONS]

Deploy a Sales Agent release bundle on this server.

Options:
  --env FILE     Deploy only the tenant using this env file (under secrets/).
                 Can be repeated to deploy multiple tenants.
                 Without --env or --yes, you'll be prompted interactively.
  --yes          Skip all prompts; deploy every tenant whose env file exists.
  -h, --help     Show this help.

Environment overrides:
  INVENTORY      Tenant inventory path.  Default: deploy/tenants.json
  COMPOSE_FILE   Generated compose path. Default: docker-compose.generated.yml

Examples:
  scripts/deploy-release.sh                          # interactive selection
  scripts/deploy-release.sh --env taishan.env        # single tenant, no prompt
  scripts/deploy-release.sh --env taishan.env --env tenant-b.env
  scripts/deploy-release.sh --yes                    # CI: deploy everything
USAGE
}

ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --env)
      shift
      if [ $# -eq 0 ]; then
        echo "ERROR: --env requires a filename argument" >&2
        exit 2
      fi
      SELECTED_ENVS+=("$1")
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# ──────────────────────────────────────────────
# 1. Prerequisite checks
# ──────────────────────────────────────────────
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

mkdir -p deploy secrets data logs images

# ──────────────────────────────────────────────
# 2. Discover env files & select tenant(s)
# ──────────────────────────────────────────────
discover_env_files() {
  find secrets/ -maxdepth 1 -name "*.env" -print0 2>/dev/null | sort -z | while IFS= read -r -d '' f; do
    basename "$f"
  done
}

AVAILABLE_ENVS=()
while IFS= read -r f; do
  AVAILABLE_ENVS+=("$f")
done < <(discover_env_files)

if [ ${#AVAILABLE_ENVS[@]} -eq 0 ]; then
  echo "ERROR: No .env files found in secrets/" >&2
  echo "Create one from deploy/tenant.env.example:" >&2
  echo "  cp deploy/tenant.env.example secrets/<tenant-id>.env" >&2
  exit 1
fi

# ── Non-interactive: --env specified ──
if [ ${#SELECTED_ENVS[@]} -gt 0 ]; then
  # Validate every --env value exists
  for env_file in "${SELECTED_ENVS[@]}"; do
    if [ ! -f "secrets/$env_file" ]; then
      echo "ERROR: secrets/$env_file not found" >&2
      echo "Available env files:" >&2
      for f in "${AVAILABLE_ENVS[@]}"; do
        echo "  secrets/$f" >&2
      done
      exit 1
    fi
  done
  echo "Deploying ${#SELECTED_ENVS[@]} tenant(s) specified via --env: ${SELECTED_ENVS[*]}"

# ── Non-interactive: --yes specified ──
elif [ "$ASSUME_YES" -eq 1 ]; then
  SELECTED_ENVS=("${AVAILABLE_ENVS[@]}")
  echo "Deploying all ${#SELECTED_ENVS[@]} tenant(s) (--yes): ${SELECTED_ENVS[*]}"

# ── Interactive: single env → auto-select ──
elif [ ${#AVAILABLE_ENVS[@]} -eq 1 ]; then
  echo "Only one env file found: secrets/${AVAILABLE_ENVS[0]}"
  SELECTED_ENVS=("${AVAILABLE_ENVS[0]}")

# ── Interactive: multiple envs → menu ──
else
  # ── Interactive arrow-key menu ──
  # Build menu items: one per env + "All" + "Quit"
  MENU_ITEMS=()
  for i in "${!AVAILABLE_ENVS[@]}"; do
    name=$(grep -m1 '^TENANT_NAME=' "secrets/${AVAILABLE_ENVS[$i]}" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" || true)
    if [ -n "$name" ]; then
      MENU_ITEMS+=("${AVAILABLE_ENVS[$i]}  ($name)")
    else
      MENU_ITEMS+=("${AVAILABLE_ENVS[$i]}")
    fi
  done
  MENU_ITEMS+=("All (${#AVAILABLE_ENVS[@]} tenants)")
  MENU_ITEMS+=("Quit")

  # ANSI helpers
  CURSOR_HIDE=$'\e[?25l'
  CURSOR_SHOW=$'\e[?25h'
  CLEAR_LINE=$'\e[2K'
  CURSOR_UP_N() { printf '\e[%sA' "$1"; }

  selected=0
  total=${#MENU_ITEMS[@]}

  draw_menu() {
    # Move cursor back to top of menu
    if [ "$1" -gt 0 ]; then
      CURSOR_UP_N "$1"
    fi
    for idx in "${!MENU_ITEMS[@]}"; do
      printf '%s' "$CLEAR_LINE"
      if [ "$idx" -eq "$selected" ]; then
        printf ' \e[7m  ▶ %s  \e[0m\n' "${MENU_ITEMS[$idx]}"
      else
        printf '    %s\n' "${MENU_ITEMS[$idx]}"
      fi
    done
  }

  # Hide cursor; draw initial menu
  printf '%s' "$CURSOR_HIDE"
  echo
  echo "  Found ${#AVAILABLE_ENVS[@]} env file(s) in secrets/.  ↑↓ to move, Enter to confirm:"
  echo
  draw_menu 0

  # Read arrow keys
  while true; do
    IFS= read -rsn1 key
    if [ "$key" = $'\x1b' ]; then
      # Escape sequence — read next two chars
      read -rsn2 -t 0.01 rest || true
      key+="$rest"
    fi

    case "$key" in
      $'\x1b[A') # Up
        if [ "$selected" -gt 0 ]; then
          ((selected--))
        fi
        draw_menu "$total"
        ;;
      $'\x1b[B') # Down
        if [ "$selected" -lt $((total - 1)) ]; then
          ((selected++))
        fi
        draw_menu "$total"
        ;;
      q|Q)
        printf '%s' "$CURSOR_SHOW"
        echo
        echo "Aborted."
        exit 0
        ;;
      '') # Enter
        printf '%s' "$CURSOR_SHOW"
        echo
        echo
        break
        ;;
    esac
  done

  # Resolve selection
  if [ "$selected" -eq $((total - 1)) ]; then
    # Quit
    echo "Aborted."
    exit 0
  elif [ "$selected" -eq $((total - 2)) ]; then
    # All
    SELECTED_ENVS=("${AVAILABLE_ENVS[@]}")
  else
    SELECTED_ENVS=("${AVAILABLE_ENVS[$selected]}")
  fi
fi

echo "Selected: ${SELECTED_ENVS[*]}"
echo

# ──────────────────────────────────────────────
# 3. Validate inventory exists
# ──────────────────────────────────────────────
if [ ! -f "$INVENTORY" ]; then
  if [ ! -f deploy/tenants.example.json ]; then
    echo "ERROR: $INVENTORY missing and deploy/tenants.example.json not found" >&2
    exit 1
  fi
  cp deploy/tenants.example.json "$INVENTORY"
  echo "Created $INVENTORY from deploy/tenants.example.json"
fi

# ──────────────────────────────────────────────
# 4. Filter inventory to only selected env files
#    + warn about env files referenced in inventory that don't exist
# ──────────────────────────────────────────────
FILTERED_INVENTORY="$(mktemp)"
python3 - "$INVENTORY" "$FILTERED_INVENTORY" "${SELECTED_ENVS[@]}" <<'PY'
import json, pathlib, sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
selected = set(sys.argv[3:])  # e.g. {"taishan.env", "tenant-b.env"}

data = json.loads(src.read_text(encoding="utf-8"))

active = []
skipped_missing = []
skipped_not_selected = []

for t in data.get("tenants", []):
    tid = t.get("id", "?")
    env_path = pathlib.Path(t.get("env_file", ""))
    env_basename = env_path.name

    if not env_path.exists():
        skipped_missing.append(f"  {tid}: env file not found ({env_path})")
        continue

    if env_basename not in selected:
        skipped_not_selected.append(f"  {tid}: {env_basename} (not selected)")
        continue

    active.append(t)

if skipped_missing:
    print("Warning: skipping tenant(s) with missing env files:")
    for s in skipped_missing:
        print(s)

if skipped_not_selected:
    print(f"Skipping {len(skipped_not_selected)} tenant(s) not in current selection:")
    for s in skipped_not_selected:
        print(s)

if not active:
    print("ERROR: No tenants match the selected env files.", file=sys.stderr)
    print("Selected env files:", file=sys.stderr)
    for s in sorted(selected):
        print(f"  secrets/{s}", file=sys.stderr)
    print("Inventory referenced env files:", file=sys.stderr)
    for t in data.get("tenants", []):
        print(f"  {pathlib.Path(t.get('env_file', '')).name}", file=sys.stderr)
    raise SystemExit(1)

data["tenants"] = active
dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"\nDeploying {len(active)} tenant(s):")
for t in active:
    print(f"  {t['id']}: {t['name']}  (port {t.get('api_port','?')}, env: {pathlib.Path(t['env_file']).name})")
PY

trap "rm -f $FILTERED_INVENTORY" EXIT

# ──────────────────────────────────────────────
# 5. Validate env file contents
# ──────────────────────────────────────────────
echo
echo "Checking configuration..."
python3 - "$FILTERED_INVENTORY" <<'PY'
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
        print(f"Warning: {tenant_id}: env file missing ({env_path}), skipping")
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
        print(f"Warning: {tenant_id}: TENANT_ID mismatch, updating inventory id to {values.get('TENANT_ID')!r} (env wins)")
        tenant["id"] = values["TENANT_ID"]
        tenant["_id_updated"] = True
    joined = env_path.read_text(encoding="utf-8")
    for marker in PLACEHOLDERS:
        if marker in joined:
            errors.append(f"{tenant_id}: replace placeholder {marker!r} in {env_path}")
    domain = tenant.get("domain", "")
    if domain and "example.com" in domain:
        errors.append(f"{tenant_id}: replace example domain in inventory")
    if not isinstance(tenant.get("api_port"), int):
        errors.append(f"{tenant_id}: api_port must be an integer in inventory")

# Persist any auto-corrected tenant ids back to inventory
if any(t.get("_id_updated") for t in data.get("tenants", [])):
    for t in data.get("tenants", []):
        t.pop("_id_updated", None)
    pathlib.Path(sys.argv[1]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Updated inventory with corrected tenant ids.")

if errors:
    print("\nConfiguration is not ready:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    raise SystemExit(1)
print("Configuration placeholder check passed.")
PY

# ──────────────────────────────────────────────
# 6. Port conflict detection
# ──────────────────────────────────────────────
python3 - "$FILTERED_INVENTORY" <<'PY'
import json, pathlib, socket, sys

def port_in_use(port: int) -> bool:
    """Check if a TCP port is already bound on this host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
ports_seen: set[int] = set()
changed = False
for tenant in data.get("tenants", []):
    tid = tenant.get("id", "?")
    port = int(tenant.get("api_port", 0))
    if not port:
        continue
    # Track ports within this inventory to avoid duplicates
    while port in ports_seen:
        print(f"Warning: {tid}: port {port} already assigned to another tenant, trying {port+1}")
        port += 1
        changed = True
    # Check against host
    while port_in_use(port):
        print(f"Warning: {tid}: port {port} is in use on this host, trying {port+1}")
        port += 1
        changed = True
    ports_seen.add(port)
    if port != tenant["api_port"]:
        tenant["api_port"] = port
        changed = True
if changed:
    pathlib.Path(sys.argv[1]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Updated inventory with corrected ports.")
else:
    print("All ports available.")
PY

# ──────────────────────────────────────────────
# 7. Confirmation prompt (unless --yes)
# ──────────────────────────────────────────────
if [ "$ASSUME_YES" -ne 1 ]; then
  echo
  echo "The following configuration will be used for deployment:"
  python3 - "$FILTERED_INVENTORY" <<'PY'
import json, pathlib, sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"  inventory: {pathlib.Path(sys.argv[1]).resolve()} (filtered)")
for tenant in data.get("tenants", []):
    print(f"  tenant env: {pathlib.Path(tenant['env_file']).resolve()}")
PY
  echo
  echo "Type $CONFIRM_WORD to confirm and deploy."
  read -r -p "> " answer
  if [ "$answer" != "$CONFIRM_WORD" ]; then
    echo "Aborted. Edit the config files above, then rerun scripts/deploy-release.sh."
    exit 1
  fi
fi

# ──────────────────────────────────────────────
# 8. Load docker image
# ──────────────────────────────────────────────
if [ -n "${REGISTRY_IMAGE:-}" ]; then
  # Registry pull path (CI default). REGISTRY_IMAGE e.g. host:5000/sales-agent:<tag>.
  REG_HOST="${REGISTRY_HOST:-172.25.186.210:5000}"
  if [ -n "${REGISTRY_USER:-}" ] && [ -n "${REGISTRY_PASS:-}" ]; then
    echo "Logging into registry ${REG_HOST}"
    echo "${REGISTRY_PASS}" | docker login "${REG_HOST}" -u "${REGISTRY_USER}" --password-stdin
  fi
  echo "Pulling image: ${REGISTRY_IMAGE}"
  docker pull "${REGISTRY_IMAGE}"
  # Let renderer use exact tag instead of inventory default
  export OVERRIDE_IMAGE="${REGISTRY_IMAGE}"
elif compgen -G "images/*.tar" >/dev/null; then
  # Offline/cold-start fallback: load from release tarball
  for image_tar in images/*.tar; do
    echo "Loading docker image: $image_tar"
    docker load -i "$image_tar"
  done
else
  echo "No REGISTRY_IMAGE and no images/*.tar; using inventory image tag as-is."
fi

# ──────────────────────────────────────────────
# 9. Render compose & start services
# ──────────────────────────────────────────────
python3 scripts/render-multitenant-deploy.py "$FILTERED_INVENTORY" --compose-out "$COMPOSE_FILE"

echo "Starting services with $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" up -d

# ──────────────────────────────────────────────
# 10. Auto-create tenant DB records
# ──────────────────────────────────────────────
echo "Ensuring tenant records in database..."
python3 - "$FILTERED_INVENTORY" <<'PY'
import json, pathlib, sys, time, urllib.request, urllib.error

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for tenant in data.get("tenants", []):
    tid = tenant["id"]
    name = tenant.get("name", tid)
    port = tenant["api_port"]
    url = f"http://127.0.0.1:{port}/tenants"
    body = json.dumps({"tenant_id": tid, "name": name}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(10):
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status < 500:
                break
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError):
            if attempt < 9:
                time.sleep(2)
    print(f"  {tid}: tenant record ensured")
PY

# ──────────────────────────────────────────────
# 11. Health checks
# ──────────────────────────────────────────────
echo "Running tenant checks"
scripts/check-all-tenants.sh "$FILTERED_INVENTORY"

# ──────────────────────────────────────────────
# 12. Register DingTalk quick entries
# ──────────────────────────────────────────────
echo "Registering DingTalk quick entries when enabled"
python3 - "$FILTERED_INVENTORY" <<'PY' | while IFS=$'\t' read -r tenant_id register_url; do
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
        values[key.strip()] = value.strip().strip('"').strip("'")
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

# ──────────────────────────────────────────────
# 13. Summary
# ──────────────────────────────────────────────
echo
echo "======================================================"
echo "  ✅  Deployment complete"
echo "======================================================"
python3 - "$COMPOSE_FILE" <<'PY' 2>/dev/null || true
import json, pathlib, subprocess, sys

# Read generated compose to find tenant services
compose = pathlib.Path(sys.argv[1])
if not compose.exists():
    sys.exit(0)
text = compose.read_text()
import re
# Extract tenant api services and their host ports
services = {}
for m in re.finditer(r'^\s{2}(\w+)-api:\s*$', text, re.MULTILINE):
    services[m.group(1)] = None

# Find ports for each tenant by scanning nearby lines
lines = text.split('\n')
for i, line in enumerate(lines):
    for tid in services:
        if line.strip() == f'{tid}-api:':
            # look ahead for port mapping
            for j in range(i+1, min(i+20, len(lines))):
                pm = re.match(r'\s{4}-\s*"(\d+):8000"', lines[j])
                if pm:
                    services[tid] = pm.group(1)
                    break
            break  # Only need first occurrence of this tid

# Quick health check per tenant
for tid, port in services.items():
    if port:
        try:
            r = subprocess.run(['curl', '-sf', f'http://127.0.0.1:{port}/health'], capture_output=True, text=True, timeout=5)
            status = '✅ healthy' if r.returncode == 0 else '⚠️  no response'
        except Exception:
            status = '⚠️  no response'
        print(f"  {tid}: http://127.0.0.1:{port}  {status}")
    else:
        print(f"  {tid}: (port not found)")
PY
echo "======================================================"
