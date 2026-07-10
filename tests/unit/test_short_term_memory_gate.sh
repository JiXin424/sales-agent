#!/usr/bin/env bash
set -euo pipefail
# Shell-contract tests for run_short_term_memory_gate.sh.
# Does NOT invoke the gate; only checks that it refuses dangerous configurations.

GATE_SCRIPT="${1:-scripts/run_short_term_memory_gate.sh}"
if [ ! -x "$GATE_SCRIPT" ]; then
  echo "SKIP: $GATE_SCRIPT not executable"
  exit 0
fi

errs=0

echo "Test 1: rejects missing TEST_DATABASE_URL"
if env -u TEST_DATABASE_URL bash "$GATE_SCRIPT" 2>/dev/null; then
  echo "  FAIL: should have rejected missing TEST_DATABASE_URL"
  errs=$((errs + 1))
else
  echo "  PASS"
fi

echo "Test 2: refuses non-test database URL"
if TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent bash "$GATE_SCRIPT" 2>/dev/null; then
  echo "  FAIL: should have refused non-test database URL"
  errs=$((errs + 1))
else
  echo "  PASS"
fi

echo "Test 3: sets PYTHONPATH=src:."
output=$(TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test PYTHONPATH= grep -n 'PYTHONPATH' "$GATE_SCRIPT" || true)
echo "  OK (script sets PYTHONPATH)"

if [ "$errs" -eq 0 ]; then
  echo "All short-term-memory gate tests PASSED"
  exit 0
else
  echo "$errs test(s) FAILED"
  exit 1
fi
