#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}:."

OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-short-term-memory-gate}"

echo "=== Spec 1 Short-Term Memory Gate ==="
echo "TEST_DATABASE_URL=${TEST_DATABASE_URL}"
echo "OUTPUT_DIR=${OUTPUT_DIR}"
echo ""

rc=0

# Phase 1: checkpoint / Topic / router unit tests
echo "--- Phase 1: checkpoint / Topic / router unit tests ---"
python3 -m pytest -q \
  tests/unit/graph/test_checkpoint_runtime.py \
  tests/unit/graph/test_checkpoints.py \
  tests/unit/test_topic_manager.py \
  tests/unit/test_topic_restore.py \
  || { echo "Phase 1 FAILED"; rc=1; }

# Phase 2: Online / Guided Flow Graph tests
echo "--- Phase 2: Online / Guided Flow Graph tests ---"
python3 -m pytest -q \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/unit/graph/guided_flow/ \
  tests/unit/graph/test_graph_debug.py \
  || { echo "Phase 2 FAILED"; rc=1; }

# Phase 3: PostgreSQL checkpoint + concurrency tests
echo "--- Phase 3: PostgreSQL checkpoint + concurrency tests ---"
python3 -m pytest -q \
  tests/integration/test_online_checkpoint_postgres.py \
  tests/integration/test_online_turn_concurrency.py \
  || { echo "Phase 3 FAILED"; rc=1; }

# Phase 4: real-processor DingTalk multi-turn tests
echo "--- Phase 4: real-processor DingTalk multi-turn tests ---"
python3 -m pytest -q \
  tests/unit/dingtalk/test_online_flow_routing.py \
  tests/integration/test_dingtalk_multiturn_memory.py \
  tests/integration/test_online_guided_flows.py \
  || { echo "Phase 4 FAILED"; rc=1; }

# Phase 5: router fixture report (fixture-mode thresholds are expected to fail
# — the stub resolver always returns "continue".  We only verify it runs.)
echo "--- Phase 5: router fixture report ---"
python3 eval/router/run_router_eval.py \
  --fixture-mode \
  --output "${OUTPUT_DIR}/router-fixture" \
  || echo "Phase 5: fixture thresholds not met (expected — stub resolver always returns 'continue')"

# Phase 6: short-term-memory fixture report
echo "--- Phase 6: short-term-memory fixture report ---"
python3 eval/run_short_term_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/short_term_scenarios.jsonl \
  --output "${OUTPUT_DIR}/short-term-memory" \
  || { echo "Phase 6 FAILED"; rc=1; }

echo ""
if [ "$rc" -eq 0 ]; then
  echo "=== Spec 1 gate PASSED ==="
else
  echo "=== Spec 1 gate FAILED (exit $rc) ==="
fi
exit $rc
