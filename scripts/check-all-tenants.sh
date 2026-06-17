#!/usr/bin/env bash
set -euo pipefail

INVENTORY="${1:-deploy/tenants.json}"

python3 - "$INVENTORY" <<'PY' | while IFS=$'\t' read -r tenant_id api_port; do
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

for tenant in data["tenants"]:
    roles = set(tenant.get("roles") or ["api", "stream", "worker"])
    if {"api", "stream", "worker"}.issubset(roles):
        print(f"{tenant['id']}\t{tenant['api_port']}")
PY
  scripts/check-tenant.sh "$tenant_id" "$api_port"
done
