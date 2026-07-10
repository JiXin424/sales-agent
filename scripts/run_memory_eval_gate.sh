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

# Phase B: graph multiturn — structural/isolation correctness only (§3.2).
# The deterministic model double can never meet §6 quality thresholds
# (explicit_operation_accuracy >= 1.0, recall >= 0.9), so rc=1 is expected.
# Quality gates are enforced by model-multiturn (real model, Spec §3.3/§6).
# rc=3 means a SAFETY/isolation gate failed (cross_scope_leakage /
# prohibited_memory_write_count / assistant_output_to_user_memory) — that is a
# real isolation regression and must hard-fail the gate even though we tolerate
# the rc=1 quality miss under the double.
echo "--- Phase B: graph-multiturn (§3.2) ---"
set +e
python3 eval/memory_eval/runner.py graph-multiturn \
  --dataset eval/memory/datasets/multiturn_v1.jsonl \
  --output "${OUTPUT_DIR}/graph-multiturn"
phase_b_rc=$?
set -e
case "$phase_b_rc" in
  0) ;;
  1) echo "Phase B: graph-multiturn quality thresholds not met under the deterministic double (expected — quality gates are enforced by model-multiturn, Spec §3.3/§6)" ;;
  3) echo "Phase B FAILED: safety/isolation gate violated"; rc=1 ;;
  *) echo "Phase B FAILED"; rc=1 ;;
esac

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
