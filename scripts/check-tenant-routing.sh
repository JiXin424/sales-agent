#!/bin/bash
# Verify subdomain → backend routing health for a tenant.
# Usage: check-tenant-routing.sh <subdomain> [tenant_id]
#   subdomain:  full subdomain, e.g. songbai.aijiaolian.com.cn
#   tenant_id:  optional; defaults to first label of subdomain
#
# Exit 0 if backend reachable (any HTTP response except 502/000),
# exit 1 if unreachable.
set -euo pipefail

SUBDOMAIN="${1:?usage: check-tenant-routing.sh <subdomain> [tenant_id]}"
TENANT_ID="${2:-${SUBDOMAIN%%.*}}"
URL="https://${SUBDOMAIN}/integrations/dingtalk/t/${TENANT_ID}/quick"

echo "→ probing ${URL} ..."
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 "${URL}" 2>&1) || {
    echo "FAIL: curl error reaching ${SUBDOMAIN}"
    exit 1
}

if [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "000" ]; then
    echo "FAIL: ${SUBDOMAIN} → backend unreachable (HTTP ${HTTP_CODE})"
    exit 1
fi

echo "OK: ${SUBDOMAIN} → backend reachable (HTTP ${HTTP_CODE})"
