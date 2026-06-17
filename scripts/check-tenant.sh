#!/usr/bin/env bash
set -euo pipefail

TENANT_ID="${1:?usage: scripts/check-tenant.sh <tenant_id> <api_port>}"
API_PORT="${2:?usage: scripts/check-tenant.sh <tenant_id> <api_port>}"

for role in api stream worker; do
  name="sales-agent-${TENANT_ID}-${role}"
  if docker ps --format '{{.Names}}' | grep -qx "${name}"; then
    echo "ok: ${name} running"
  else
    echo "error: ${name} is not running" >&2
    exit 1
  fi
done

curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null
echo "ok: ${TENANT_ID} /health"

curl -fsS "http://127.0.0.1:${API_PORT}/ready" >/dev/null
echo "ok: ${TENANT_ID} /ready"
