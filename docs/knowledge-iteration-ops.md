# Knowledge Iteration Operations Runbook

## Overview

The knowledge evaluation optimization loop converts evaluation failures into
attributable, testable improvements to routing, retrieval, or documents.
Every candidate is built and evaluated in isolation before human approval
and production publication.

## Prerequisites

- PostgreSQL with pgvector
- LangGraph with PostgresSaver checkpointer
- Alembic migrations applied (`alembic upgrade head`)
- Agent `feature_flags_json` includes `"knowledge_iteration": true`

## Quick Start

### Web Console

1. Navigate to an Agent → **知识迭代** in the sidebar.
2. Click **Start New Iteration** — this pins the baseline release, fixed
   suite, and budget.
3. Watch the **Attribution** panel for failure diagnoses.
4. Review **Candidates** — each changes only one domain (router, retrieval,
   or document).
5. **Publish** an approved candidate to switch the production runtime.
6. New exploration questions appear in **Questions** after publication.

### CLI

```bash
# Start an iteration
sales-agent iteration start --agent a1 --fixed-suite fixed_v3

# List iterations
sales-agent iteration list --agent a1

# Approve
sales-agent iteration approve --agent a1 --iteration i1

# Publish (requires --yes)
sales-agent iteration publish --agent a1 --candidate c1 --yes

# Rollback (requires --yes)
sales-agent iteration rollback --agent a1 --release r1 --yes

# Fork a checkpoint
sales-agent iteration checkpoint-fork --agent a1 --checkpoint cp1 --candidate c1
```

## Release Gates

| Gate | Type | Configurable |
|------|------|-------------|
| Target improvement ≥ 20% | Soft | Per-tenant |
| Fixed-suite regression ≤ 2% | Soft | Per-tenant |
| Critical fact errors = 0 | **Hard** | No |
| Fabrication count increase = 0 | **Hard** | No |
| Safety violations = 0 | **Hard** | No |
| Cross-tenant leakage = 0 | **Hard** | No |
| Latency p95 < 30s | Soft | Per-tenant |
| Token total < 500k | Soft | Per-tenant |

Hard gates block release regardless of aggregate score improvement.

## Rollback

Rollback creates a new immutable release whose manifest matches the target
historical release, then atomically switches the runtime binding. Rollback
**never deletes history** — it is a new auditable release with
`rollback_of_release_id`.

## Worker Lease Recovery

The optimization worker uses PostgreSQL `FOR UPDATE SKIP LOCKED` leasing:
- Lease duration: 5 minutes
- Heartbeat: every 60 seconds
- Max attempts: 3 per job
- Expired leases are automatically reclaimed by other workers

## Artifact Retention

Knowledge versions, document revisions, chunks, and profiles are
soft-retired. Garbage collection may delete them only when no release,
checkpoint, eval run, iteration, or audit-retention policy references them.

## Feature Flag Rollout

Set `feature_flags_json["knowledge_iteration"] = true` for one
non-production Agent first:

```json
{"knowledge_iteration": true}
```

Then:
1. Bootstrap its baseline release
2. Run a 10-case fixed suite
3. Publish a no-op candidate
4. Verify the new exploration suite
5. Roll back to the baseline release
6. Confirm release events and checkpoint references remain queryable

## Rule

New exploration scores **never directly trigger rollback**. Only
reproducible regressions on the stable fixed suite may trigger rollback
approval.

## PostgreSQL Backup

Always back up before migration:

```bash
pg_dump -U sales_agent -h localhost -p 5433 sales_agent > backup_$(date +%Y%m%d).sql
```

Key tables: `optimization_releases`, `agent_runtime_bindings`,
`release_events`, `optimization_iterations`, `optimization_candidates`,
`iteration_graph_checkpoints`, `knowledge_facts`.

---

## Iteration Observability & Effect Reports

*Added 2026-07-02*

### Migration Order

The observability schema is in migration `0008_iteration_observability_reports`.
It adds four new tables (`iteration_events`, `iteration_reports`,
`iteration_report_metrics`, `iteration_report_cases`) and extends
`optimization_iterations` with lifecycle, event cursor, and time-travel columns.

**Deploy order:**
1. Run `alembic upgrade head` (or let `init_db()` auto-migrate on startup).
2. Existing iterations continue to work; new columns default to NULL/0.
3. No data backfill is required for existing rows.

### Report Root & Retention

Report artifacts are written under `eval/results/iterations/{tenant}/{agent}/{iteration}/{report}/`.
These are derived from the authoritative JSON stored in the database.
Artifacts can be regenerated at any time via the API or CLI without
re-running evaluation.

Retention policy: retain for the lifetime of the iteration.
Delete artifacts alongside iteration archival.

### Status Lifecycle

| Status | Meaning |
|--------|---------|
| `created` | Iteration record created |
| `generating_cases` | Building exploration questions |
| `evaluating_baseline` | Running baseline fixed suite |
| `diagnosing` | Attributing failures to root causes |
| `generating_candidates` | Proposing optimization candidates |
| `evaluating_candidates` | Running candidate eval gates |
| `awaiting_approval` | Waiting for human operator approval |
| `publishing` | Creating a release from approved candidate |
| `post_publish_evaluating` | Running fixed suite against published release |
| `generating_final_report` | Computing final effect report |
| `completed` | Final report ready; iteration is done |

**Publish is not completion.** An iteration reaches `completed` only after
the post-publish fixed evaluation finishes and a final effect report is
generated.

### Event Replay & SSE

- **Replay**: `GET /iterations/{id}/events?after_sequence=N&limit=M`
- **Long poll**: `GET /iterations/{id}/events/wait?after_sequence=N&timeout_seconds=T`
- **SSE**: `GET /iterations/{id}/events/stream` (supports `Last-Event-ID` for reconnect)

Heartbeat comments every 15 seconds keep the SSE connection alive.
The stream closes when the iteration reaches a terminal state.

### CLI Watch & Export

```bash
# Watch events with cursor
sales-agent optimization watch --agent A1 --iteration I1 --after-sequence 5

# Export report artifact
sales-agent optimization report --agent A1 --iteration I1 --report-id R1 --format markdown --output report.md

# View trends
sales-agent optimization trends --agent A1 --limit 10
```

### Report Rebuild

Reports are idempotent. If evaluation data or the formula version changes,
regenerate with:

```python
from sales_agent.optimization.reporting.service import IterationReportService
service = IterationReportService(db)
report = await service.generate_final(
    tenant_id="...", agent_id="...", iteration_id="...",
    release_id="...", baseline_eval_run_id="...",
    post_publish_eval_run_id="...",
)
```

A changed formula version or different input hash creates a new report version.
Identical inputs return the existing completed report.

### Hard Gates

| Gate | Source | Effect |
|------|--------|--------|
| `tenant_leakage` | Cross-tenant data in response | `do_not_publish` / `rollback_recommended` |
| `critical_fact_error` | Factual accuracy failure | `do_not_publish` / `rollback_recommended` |
| `unsafe_response` | Safety metric failure | `do_not_publish` / `rollback_recommended` |
| `increased_fabrication` | More fabrication on unanswerable | `do_not_publish` / `rollback_recommended` |

Hard gates override the composite score. A failed gate on a candidate
produces `do_not_publish`; on a final report, `rollback_recommended`.

### Time Travel Lineage

Forked iterations record `parent_iteration_id` and `forked_from_checkpoint_id`.
Original and forked executions have distinct IDs and event streams.
Reports bind to exact release/checkpoint/eval inputs and remain immutable.

### Rollback Procedure

1. Identify a previous healthy release via the trends API or CLI.
2. Run a rollback: `POST /agents/{A}/optimization/releases/rollback` with `target_release_id`.
3. Rollback releases are typed as `OptimizationRelease` with `rollback_of_release_id`.
4. Rollback reports remain readable and identify both the failed release and the new rollback release.

### Verification Commands

```bash
# Backend tests
pytest tests/unit/optimization/ tests/unit/test_optimization_cli.py -q

# Migration check
alembic upgrade head && alembic current
# Expected: current revision is 0008_iteration_observability_reports

# Frontend build
cd console && npm run build

# Task validation
python3 ./.trellis/scripts/task.py validate 07-02-iteration-observability-reports
```
