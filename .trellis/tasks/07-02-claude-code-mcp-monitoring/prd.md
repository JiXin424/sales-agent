# Claude Code MCP Iteration Monitoring — PRD

## Problem

Operators want to invoke the optimization Agent from Claude Code, keep watching long-running iterations, inspect reports, request constrained alternatives, and rerun evaluations without copying IDs and logs between tools. Direct database access or broad production mutation would violate tenant isolation and human approval boundaries.

## Goal

Expose the unified iteration Observability API through a tenant-scoped remote MCP server for Claude Code. Claude Code may start, monitor, inspect, analyze, request another constrained candidate, and rerun an evaluation. It may not approve, publish, or roll back a release.

## Dependency

The `07-02-iteration-observability-reports` child task must be complete. MCP is an adapter and must not invent a second event, status, candidate, report, or trend implementation.

## Functional Requirements

1. Provide a remote Streamable HTTP MCP endpoint suitable for Claude Code.
2. Authenticate every connection with a revocable tenant-scoped credential; store only token hashes.
3. Derive tenant from the credential, never from model-supplied arguments.
4. Restrict credentials to an Agent allowlist and scopes.
5. Provide tools to start an iteration, read status, wait for updates, list/compare candidates, read reports/trends, request an alternative candidate, and rerun an evaluation.
6. Do not register approve, publish, rollback, token administration, arbitrary SQL, arbitrary URL, arbitrary file, or unrestricted workflow tools.
7. Enforce the same deny policy in the API authorization layer, not only in MCP tool discovery.
8. Long monitoring uses cursor-based 30-second waits and compact incremental results; reconnect resumes from the caller's last sequence.
9. Expose status, report, candidates, and trend as MCP resources and monitoring/report-analysis workflows as MCP prompts.
10. Audit every mutating MCP call with actor type, credential subject, MCP client/session metadata, idempotency key, and result.
11. Keep default output compact; full cases/events require pagination or explicit resource reads.

## Permission Matrix

| Capability | Claude Code MCP |
|---|---|
| Start iteration | allowed |
| Read status/events/reports/trends | allowed |
| Request constrained alternative candidate | allowed |
| Rerun targeted/fixed candidate evaluation | allowed |
| Approve candidate/iteration | denied |
| Publish release | denied |
| Roll back release | denied |
| Access another tenant or unlisted Agent | denied |

## Transport and Client Experience

The first release uses remote Streamable HTTP. Claude Code registers the HTTPS URL with an Authorization header/helper. A typical session calls `start_iteration`, repeatedly calls `wait_for_iteration_update` with the returned cursor, fetches the final Markdown/JSON report, and summarizes it. Optional Claude Channel push notifications are a later enhancement and cannot be required for correctness.

## Acceptance Criteria

1. Claude Code can connect, discover allowed tools/resources/prompts, and call a read tool.
2. A valid credential accesses only its tenant and Agent allowlist.
3. Supplying another Agent ID or an iteration owned by another tenant cannot cross the boundary.
4. No MCP schema exposes approval, publish, rollback, arbitrary endpoint, or arbitrary SQL capabilities.
5. Direct attempts with an MCP credential against approval/publish/rollback APIs return `403 human_approval_required`.
6. Retrying a mutating call with the same idempotency key creates no duplicate iteration, candidate, or eval job.
7. Monitoring resumes using `after_sequence` without duplicate semantic events.
8. Normal tool responses stay below the default output budget; verbose data is paginated.
9. Every mutating call creates an immutable audit event identifying the MCP actor.
10. MCP contract, API authorization, transport integration, and Claude Code smoke tests pass.
