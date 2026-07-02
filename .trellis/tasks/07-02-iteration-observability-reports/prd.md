# Iteration Observability and Effect Reports — PRD

## Problem

The knowledge optimization loop can produce candidates and evaluation runs, but operators cannot reliably answer three questions from one durable source: what stage is running now, whether a candidate improved the Agent, and whether the published release still improved after a fresh post-release evaluation. Existing DeepEval files are useful snapshots, but they are not tenant-scoped iteration records and cannot drive Web, CLI, and MCP consistently.

## Goal

Add a PostgreSQL-backed observability and reporting layer for every optimization iteration. It must expose ordered progress events, reproducible candidate/final reports, single-round before/after effects, and ten-round trends through one API used by Web, CLI, and the later MCP adapter.

## Functional Requirements

### Durable ordered events

1. Every state transition and material progress update appends an immutable event with tenant, Agent, iteration, monotonically increasing sequence, stage, status, progress, message, structured payload, and timestamp.
2. State mutation and its event append occur in the same database transaction.
3. Event reads support `after_sequence`, bounded page size, and a 30-second long-poll wait.
4. SSE supports replay through `Last-Event-ID`, heartbeat frames, and terminal close.
5. Event payloads contain identifiers and summaries, never raw secrets, credentials, or full document bodies.

### Reports

1. Generate `candidate_report` after candidate evaluation gates finish.
2. Generate `final_report` only after publish and a fresh post-publish evaluation finish.
3. A published iteration is not `completed` until its final report exists.
4. Reports compare pinned baseline and candidate/post-publish eval runs without executing evaluation again.
5. Reports persist aggregate metrics, per-case classifications, formula version, data snapshot hash, hard-gate outcome, artifact locations, and generation status.
6. Report generation is idempotent for `(tenant, iteration, report_type, candidate, report_version)` and can be rebuilt from persisted evaluation rows.
7. JSON is the authoritative representation. HTML, Markdown, and CSV are deterministic projections of that JSON.

### Effect model

1. The composite effect index is versioned and starts with: answer quality 30%, retrieval 30%, routing 15%, safety/refusal 15%, efficiency 10%.
2. Every group remains visible independently; the composite score never hides a regression.
3. Metric direction and normalization are explicit. Lower-is-better latency, token, cost, and error metrics are normalized before weighting.
4. Tenant leakage, critical-fact error, unsafe response, or increased fabrication on unanswerable cases are hard gates. A hard-gate failure overrides the composite recommendation.
5. Per-case output classifies `improved`, `regressed`, `unchanged`, `new`, or `error`, with evidence and rank/latency deltas where available.
6. Trend output contains the latest ten immutable final reports for the same tenant and Agent.

### Operator surfaces

1. The optimization Web page shows stage/progress, event timeline, candidate comparison, effect report, per-case regressions, and ten-round trend.
2. CLI can watch from an event cursor and export JSON, Markdown, HTML, or CSV.
3. API exposes status, events, wait, SSE, candidate reports, final reports, artifact download, and trend endpoints.

### Time travel and multi-tenancy

1. All tables and high-value indexes begin with `tenant_id`; every query requires authenticated/derived tenant context and validates Agent ownership.
2. Reports bind to immutable release IDs, eval run IDs, formula version, and snapshot hash.
3. A replay/fork produces a new execution branch and report; it never overwrites the original report or events.
4. Rollback reports remain readable and identify both the failed release and the newly activated rollback release.

## Non-Goals

- Replacing DeepEval or rerunning evaluation inside report generation.
- Allowing automatic production approval, publish, or operator rollback.
- Adding a second queue technology; the existing leased PostgreSQL job table is sufficient.
- Generating questions from real usage logs in this version.
- Building Claude Code MCP transport in this task; it is the dependent child task.

## Acceptance Criteria

1. Two concurrent event writers for one iteration receive unique ordered sequences with no duplicate committed events.
2. A transaction rollback removes both the state update and its associated event.
3. `after_sequence` and `Last-Event-ID` replay only unseen events and preserve order.
4. Cross-tenant event, report, artifact, and trend access returns 404/403 without revealing existence.
5. Candidate report shows baseline/candidate values, absolute and relative deltas, per-group scores, hard gates, and case regressions.
6. Final report is generated from a post-publish eval run and cannot be substituted by a candidate report.
7. Identical persisted inputs and formula version produce the same snapshot hash and metric output.
8. A hard-gate failure produces `do_not_publish` or `rollback_recommended` even when the composite score improves.
9. The latest-ten trend uses only final reports belonging to the same tenant and Agent.
10. Web, CLI, JSON API, and exported artifacts display values derived from the same persisted report.
11. Existing DeepEval JSON/Markdown/CSV/HTML generation remains usable.
12. Backend tests, migration upgrade, frontend tests, and frontend production build pass.
