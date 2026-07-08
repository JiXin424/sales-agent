#!/usr/bin/env bash
set -euo pipefail

# Spec 4 — Memory Evaluation & Operations release gate.
# Refuses any non-*test* database URL. Exits non-zero on any failed phase
# (release blocked). Mirrors scripts/run_short_term_memory_gate.sh.

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}:."

OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-memory-eval-gate}"

echo "=== Spec 4 Memory Evaluation Gate ==="
echo "TEST_DATABASE_URL=${TEST_DATABASE_URL}"
echo "OUTPUT_DIR=${OUTPUT_DIR}"
echo ""

rc=0

# Phase A: deterministic unit/property suite (§3.1) — no model, no DB.
echo "--- Phase A: unit-memory (§3.1) ---"
python3 eval/memory_eval/runner.py unit-memory \
  --output "${OUTPUT_DIR}/unit-memory" \
  || { echo "Phase A FAILED"; rc=1; }

# Phase B: graph multiturn against the real Online Graph + PG (§3.2)
echo "--- Phase B: graph-multiturn (§3.2) ---"
python3 eval/memory_eval/runner.py graph-multiturn \
  --dataset eval/memory/datasets/multiturn_v1.jsonl \
  --output "${OUTPUT_DIR}/graph-multiturn" \
  || { echo "Phase B FAILED"; rc=1; }

# Phase C: dataset coverage + fail-closed release gates (§4, §6)
echo "--- Phase C: dataset coverage + release gates (§4, §6) ---"
python3 -m pytest -q \
  tests/unit/eval/test_multiturn_dataset_coverage.py \
  tests/unit/eval/test_gates.py \
  || { echo "Phase C FAILED"; rc=1; }

echo ""
if [ "$rc" -eq 0 ]; then
  echo "=== Spec 4 gate PASSED ==="
else
  echo "=== Spec 4 gate FAILED (exit $rc) ==="
fi
exit "$rc"
