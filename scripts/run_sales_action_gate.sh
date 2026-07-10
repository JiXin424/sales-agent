#!/usr/bin/env bash
# 销售动作发布门禁：unit + 集成 + fixture eval。
# 要求隔离的 PostgreSQL 测试库（URL 必须含 "test"），否则拒绝运行。
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-sales-action-gate}"
rc=0

python3 -m pytest -q tests/unit/sales_actions tests/unit/eval/test_sales_action_eval.py || rc=1
python3 -m pytest -q \
  tests/integration/test_sales_action_repository.py \
  tests/integration/test_dingtalk_sales_actions.py \
  tests/integration/test_sales_action_scheduler.py \
  tests/integration/test_sales_action_api.py || rc=1
python3 eval/run_sales_action_eval.py \
  --mode fixture \
  --dataset eval/sales_actions/action_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" || rc=1

exit "$rc"
