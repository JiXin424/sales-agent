# Memory Evaluation & Operations Runbook (Spec 4)

Operator runbook for the memory-evaluation / operations harness under
`eval/memory_eval/`. Covers the release gate, the seven CLI modes, the
fail-closed release gates, and the promote-trace feedback loop.

---

## Run the gate (every release)

```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  ./scripts/run_memory_eval_gate.sh
```

Exit non-zero = **release blocked**. The gate refuses any non-`*test*` DB URL
(safety guard against wiping prod).

Phases:

| Phase | What | Spec |
|-------|------|------|
| A | `unit-memory` — deterministic unit/property suite, no model, no DB | §3.1 |
| B | `graph-multiturn` — deterministic model double driving the real Online Graph + PG | §3.2 |
| C | dataset coverage (`test_multiturn_dataset_coverage.py`) + fail-closed release gates (`test_gates.py`) | §4, §6 |

Artifacts land under `OUTPUT_DIR` (default
`/tmp/sales-agent-memory-eval-gate`). Override with `OUTPUT_DIR=...`.

---

## CLI modes

All modes dispatch from one entry point:

```bash
python3 eval/memory_eval/runner.py <mode> [--dataset ...] [--output ...]
```

| Mode | When to run | Model / DB | Spec |
|------|-------------|-----------|------|
| `unit-memory` | every commit | none (deterministic) | §3.1 |
| `graph-multiturn` | every PR | deterministic double + PG | §3.2 |
| `model-multiturn` | nightly / on model·prompt change | real model + PG | §3.3 |
| `dingtalk-staging` | every release | staging DB (all six approved scenarios) | §3.4 |
| `compare <baseline> <candidate>` | before release | none (compares two report JSONs) | §7 |
| `online-sample` | continuous / production | prod DB, **non-blocking**, bounded `--max-threads` | §3.5 |
| `promote-trace --trace-id <id>` | on a production failure | prod DB | §9 |

Every report exposes **applicability, denominators, errors, and versions**
(§7 / §12). `compare` produces a baseline↔candidate diff with versioned
rationale for any threshold change.

---

## Release gates (§6)

The isolation / safety gates **fail closed**. Any one of these blocks the
release:

- **Zero cross-tenant / cross-agent / cross-user leakage.**
- **Zero prohibited-memory writes** (fields that must never be persisted to
  user memory).
- **Zero assistant-output → user-memory activations** (model text never
  re-enters as a stored user fact).

Rules of engagement:

- **Never lower a threshold just to pass.** A threshold change requires a
  versioned rationale recorded with the report.
- Gates are exercised in Phase C of the gate script
  (`tests/unit/eval/test_gates.py`) and must stay green on every PR.

---

## Promote a production failure into a regression (§9)

When a production trace reveals a memory/multi-turn regression, promote it
into an anonymized regression scenario:

1. **The trace is already sampled** by `online-sample` into
   `memory_eval_traces` under restricted retention (scope is stored as
   `scope_hash` — no plaintext tenant/user id).
2. **Promote it:**

   ```bash
   python3 eval/memory_eval/runner.py promote-trace \
     --trace-id <trace-id> \
     --scenario-id promoted-NNN \
     --expected '{"facts": [...], "denominators": {...}}'
   ```

   This produces an **anonymized** `MultiturnScenario` written to
   `promoted_regressions` with `status=draft` (and `anonymized=true`).
3. **Review** the draft scenario, then set `status=reviewed`.
4. **Reproduce** under the real model:

   ```bash
   python3 eval/memory_eval/runner.py model-multiturn --dataset <reviewed-set>
   ```
5. **Commit** the finalized scenario into `eval/memory/datasets/` so it
   becomes part of the versioned regression set future gates run.

---

## Dataset & schema (§4)

- Versioned multi-turn dataset: `eval/memory/datasets/multiturn_v1.jsonl`
  (42 scenarios, incl. `dingtalk-staging`-tagged entries covering the six
  approved user scenarios).
- Single-turn router datasets (`eval/router/*.jsonl`) remain in place
  untouched; a multi-turn scenario MAY reference a router case via a free-form
  tag like `router:turn_relation:trc-007`.

---

## See also

- Spec 4 plan & acceptance criteria: `.superpowers/sdd/` task briefs.
- Short-term memory runbook: [`short-term-memory.md`](short-term-memory.md).
- Long-term memory runbook: [`long-term-memory.md`](long-term-memory.md).
