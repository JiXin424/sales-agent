#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-user-profile-memory-gate}"

rc=0

python3 -m pytest -q \
  tests/unit/memory/test_profile_contracts.py \
  tests/unit/memory/test_profile_projection.py \
  tests/unit/memory/test_profile_recall.py \
  tests/unit/memory/test_profile_transparency.py \
  tests/unit/memory/test_profile_worker.py \
  || rc=1

python3 -m pytest -q \
  tests/integration/test_user_memory_profile_repository.py \
  tests/integration/test_dingtalk_profile_memory_recall.py \
  || rc=1

python3 eval/run_user_profile_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/user_profile_recall_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" \
  || rc=1

exit "$rc"
