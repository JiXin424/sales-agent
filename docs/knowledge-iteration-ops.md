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
