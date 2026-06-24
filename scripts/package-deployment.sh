#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="${IMAGE:-sales-agent:latest}"
OUTPUT_DIR="${OUTPUT_DIR:-dist}"
INCLUDE_BASE_IMAGES=0
SKIP_BUILD=0
STAMP="$(date +%Y%m%d-%H%M%S)"
PACKAGE_NAME="sales-agent-release-${STAMP}"

usage() {
  cat <<USAGE
Usage: scripts/package-deployment.sh [--image IMAGE] [--output-dir DIR] [--skip-build] [--include-base-images]

Build and package a Sales Agent deployment bundle for another server.
The bundle includes the app Docker image, deployment scripts, templates, and docs.
It does not include real secrets, tenant data, logs, or TLS private keys.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="${2:?missing image}"; shift ;;
    --output-dir) OUTPUT_DIR="${2:?missing output dir}"; shift ;;
    --skip-build) SKIP_BUILD=1 ;;
    --include-base-images) INCLUDE_BASE_IMAGES=1 ;;
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
need_cmd tar
need_cmd python3

if [ "$SKIP_BUILD" -ne 1 ]; then
  echo "Building $IMAGE"
  docker build -t "$IMAGE" .
else
  echo "Skipping docker build; packaging existing image $IMAGE"
fi

mkdir -p "$OUTPUT_DIR"
WORK_DIR="$(mktemp -d)"
BUNDLE_DIR="$WORK_DIR/$PACKAGE_NAME"
mkdir -p "$BUNDLE_DIR"/{deploy,scripts,docs/deploy,images,data,logs,secrets}

copy_file() {
  src="$1"
  dest="$2"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$BUNDLE_DIR/$dest")"
    cp "$src" "$BUNDLE_DIR/$dest"
  else
    echo "ERROR: required file missing: $src" >&2
    exit 1
  fi
}

copy_file deploy/tenant.env.example deploy/tenant.env.example
copy_file deploy/tenants.example.json deploy/tenants.example.json
copy_file deploy/DEPLOY_USAGE.md deploy/DEPLOY_USAGE.md
copy_file scripts/render-multitenant-deploy.py scripts/render-multitenant-deploy.py
copy_file scripts/check-tenant.sh scripts/check-tenant.sh
copy_file scripts/check-all-tenants.sh scripts/check-all-tenants.sh
copy_file scripts/deploy-release.sh scripts/deploy-release.sh
copy_file scripts/init-db.sql scripts/init-db.sql
copy_file docs/multitenant-deployment.md docs/deploy/multitenant-deployment.md
copy_file docs/deploy/package-and-one-command-deploy.md docs/deploy/package-and-one-command-deploy.md
if [ -f docs/deploy/clone-agent-instance.md ]; then
  copy_file docs/deploy/clone-agent-instance.md docs/deploy/clone-agent-instance.md
fi

chmod +x "$BUNDLE_DIR"/scripts/*.sh "$BUNDLE_DIR"/scripts/render-multitenant-deploy.py

echo "Saving app image: $IMAGE"
docker save "$IMAGE" -o "$BUNDLE_DIR/images/sales-agent-image.tar"

if [ "$INCLUDE_BASE_IMAGES" -eq 1 ]; then
  echo "Saving base images if present locally"
  for base_image in docker.1ms.run/pgvector/pgvector:pg16; do
    if docker image inspect "$base_image" >/dev/null 2>&1; then
      safe_name="$(echo "$base_image" | tr '/:' '__')"
      docker save "$base_image" -o "$BUNDLE_DIR/images/${safe_name}.tar"
    else
      echo "Base image not found locally, skipping: $base_image"
    fi
  done
fi

cat > "$BUNDLE_DIR/README_DEPLOY.md" <<README
# Sales Agent Release Bundle

This bundle contains:

- App Docker image: images/sales-agent-image.tar
- Deployment inventory template: deploy/tenants.example.json
- Concrete usage guide: deploy/DEPLOY_USAGE.md
- Tenant env template: deploy/tenant.env.example
- One-command deploy script: scripts/deploy-release.sh
- Generated compose renderer and health checks

First deployment on a target server:

1. Extract this archive.
2. Copy deploy/tenants.example.json to deploy/tenants.json and edit tenant ids, domains, ports, and env_file paths.
3. Copy deploy/tenant.env.example to each secrets/<tenant>.env and replace all model and DingTalk credentials.
4. Run: scripts/deploy-release.sh
5. Type DEPLOY only after the script prints the config paths and you confirm they are current.

Update deployment:

1. Extract the new archive over or next to the existing deployment directory.
2. Keep the existing deploy/tenants.json and secrets/*.env.
3. Run: scripts/deploy-release.sh
README

ARCHIVE="$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
tar -C "$WORK_DIR" -czf "$ARCHIVE" "$PACKAGE_NAME"
rm -rf "$WORK_DIR"

echo "Wrote $ARCHIVE"
echo "Copy it to the target server, extract it, edit deploy/tenants.json and secrets/*.env, then run scripts/deploy-release.sh."
