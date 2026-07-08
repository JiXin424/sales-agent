#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-long-term-memory-gate}"

rc=0

python3 -m pytest -q \
  tests/unit/memory/test_contracts.py \
  tests/unit/memory/test_policy.py \
  tests/unit/memory/test_commands.py \
  tests/unit/memory/test_extractor.py \
  tests/unit/memory/test_outbox_worker.py \
  || rc=1

python3 -m pytest -q \
  tests/integration/test_atomic_memory_repository.py \
  tests/integration/test_dingtalk_long_term_memory.py \
  || rc=1

python3 eval/run_long_term_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/long_term_atomic_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" \
  || rc=1

exit "$rc"
