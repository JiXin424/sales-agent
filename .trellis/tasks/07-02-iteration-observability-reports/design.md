# Iteration Observability and Effect Reports — Technical Design

## 1. Architecture

Optimization nodes call `IterationEventService` whenever they change durable state. Candidate and post-publish completion enqueue idempotent `report` jobs in the existing `optimization_jobs` table. `IterationReportService` reads pinned evaluation rows, computes the versioned effect model, persists normalized report rows, and invokes deterministic artifact renderers. The report service never invokes the Agent or DeepEval.

```text
Optimization LangGraph / worker
  -> state update + event append (one transaction)
  -> report job (existing PostgreSQL queue)
  -> IterationReportService
       -> eval_runs / results / metrics / traces
       -> iteration_reports / metrics / cases
       -> JSON / Markdown / HTML / CSV artifacts
  -> Observability API -> Web / CLI / later MCP
```

## 2. Persistence

### `optimization_iterations` additions

Add `event_sequence BIGINT NOT NULL DEFAULT 0`, `current_stage`, `progress_current`, `progress_total`, `selected_candidate_id`, `published_release_id`, `post_publish_eval_run_id`, `final_report_id`, `parent_iteration_id`, and `forked_from_checkpoint_id`.

### `iteration_events`

Columns: `id`, `tenant_id`, `agent_id`, `iteration_id`, `sequence_no`, `event_type`, `stage`, `status`, `progress_current`, `progress_total`, `message`, `payload_json`, `actor_type`, `actor_id`, `created_at`. Enforce unique `(tenant_id, iteration_id, sequence_no)` and indexes beginning with tenant. Allocate sequence with `event_sequence = event_sequence + 1 RETURNING event_sequence`; never use `MAX()+1`.

### `iteration_reports`

Store tenant/Agent/iteration, report type (`candidate` or `final`), candidate/release/eval references, report and formula versions, status, recommendation, effect before/after/delta, hard-gate state, summary/risk/trend JSON, data snapshot hash, artifact URIs/hashes, errors, and timestamps. Use a non-null normalized `candidate_key` so unique `(tenant_id, iteration_id, report_type, candidate_key, report_version)` is effective for final reports.

### Child rows

`iteration_report_metrics` stores group/name/direction/weight, before/after values and normalized values, deltas, thresholds, applicability, gate result, sample count, and details. `iteration_report_cases` stores case metadata, result references, before/after pass, classification, cause, score/evidence summary, rank/latency/token deltas, and details.

Large presentation payloads remain artifacts. PostgreSQL retains queryable summaries and rows plus artifact URIs and hashes.

## 3. Events and lifecycle

Stable events include iteration/stage creation, progress, completion and failure; candidate creation/evaluation/report; approval request; release publication; post-publish evaluation; final report; rollback recommendation; completion/cancellation.

Lifecycle states are `created`, `generating_cases`, `evaluating_baseline`, `diagnosing`, `generating_candidates`, `evaluating_candidates`, `awaiting_approval`, `publishing`, `post_publish_evaluating`, `generating_final_report`, and `completed`, plus recoverable/terminal failure, rollback, and cancellation states. Publication alone never sets `completed`.

Candidate gates enqueue `report:candidate:{candidate_id}:{eval_run_id}:effect-v1`. Required post-publish fixed evaluation enqueues `report:final:{release_id}:{eval_run_id}:effect-v1`. Job creation and triggering state/event share a transaction.

## 4. Effect formula v1

Group weights are answer `0.30`, retrieval `0.30`, routing `0.15`, safety/refusal `0.15`, and efficiency `0.10`. Each metric definition declares source aliases, aggregation, direction, normalization bounds, group weight, and gate.

Group score is the weighted mean of applicable metrics renormalized within the group. The 0–100 effect index is renormalized across applicable groups. Missing/invalid metrics lower coverage and never receive perfect scores. Lower-is-better metrics are normalized before aggregation.

Hard gates are any tenant leakage, new critical-fact failure, safety hard-gate failure, or increased fabrication on unanswerable cases. Recommendation precedence is `rollback_recommended` for failed final reports, `do_not_publish` for failed candidate reports, then `improved`, `neutral`, or `regressed`. Formula input uses canonical sorted JSON for `data_snapshot_hash` and records `effect-v1`.

## 5. API and streaming

- `GET .../iterations/{id}` compact status/cursors.
- `GET .../{id}/events?after_sequence=&limit=` replay.
- `GET .../{id}/events/wait?after_sequence=&timeout_seconds=` long poll.
- `GET .../{id}/events/stream` SSE with `Last-Event-ID`.
- `GET .../{id}/reports` and `GET .../{id}/reports/{report_id}`.
- `GET .../{id}/reports/{report_id}/artifacts/{format}`.
- `GET /agents/{agent}/optimization/trends?limit=10`.

SSE polls committed rows, emits 15-second heartbeat comments, and closes after terminal delivery. Long poll returns by 30 seconds with `next_sequence` and `terminal`.

## 6. Rendering, UI, and CLI

Renderers consume a typed `IterationReportDocument` and never query the database. Existing DeepEval visual conventions may be reused, but application renderers live under `sales_agent.optimization.reporting`.

The Web page adds an iteration selector, live progress/timeline, report summary cards, metric groups, regression cases, and latest-ten trend. It opens one SSE connection, replays from its last cursor, and falls back to bounded polling. CLI watch uses the same cursor API; export downloads persisted artifacts.

## 7. Time travel, recovery, rollout

Original and forked executions have distinct IDs and streams, with `parent_iteration_id` and `forked_from_checkpoint_id`. Reports bind exact release/checkpoint/eval inputs and stay immutable.

Report jobs retry independently. Artifact writes use temporary paths then atomic rename after hashing. Identical input retries finish the same report version; formula/input changes create a new version. Case/event payloads are paginated and redacted.

Rollout order is migration, event service/API, report service/renderers, worker lifecycle hooks, CLI, then Web. Existing DeepEval outputs remain unchanged. Rollback disables hooks/UI without deleting durable history.

## 8. Verification

Unit tests cover formula normalization, gates, hashes, sequencing, redaction, case classification, and renderers. PostgreSQL integration tests cover concurrency, rollback, ownership, long poll, SSE replay, job idempotency, and trends. Frontend tests cover reconnect cursor, hard risks, report rendering, and Agent-scoped calls.
