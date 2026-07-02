# Claude Code MCP Iteration Monitoring — Technical Design

## 1. Architecture

The MCP server is a thin remote adapter over the unified optimization Observability API. It does not import SQLAlchemy models or access PostgreSQL. A scoped bearer credential is forwarded to the API, which derives a principal and enforces tenant, Agent, and action scopes.

```text
Claude Code
  -> MCP Streamable HTTP /mcp
  -> sales-agent-mcp adapter
  -> HTTPS Observability API
  -> optimization services / PostgreSQL
```

Use the official Python MCP SDK stable line with `mcp>=1.27,<2`. Remote HTTP fits Ts-dev2 and reconnection; deprecated SSE transport is excluded.

## 2. Authentication and authorization

`optimization_api_credentials` stores `id`, `tenant_id`, `subject`, `token_prefix`, password-grade token hash, `agent_ids_json`, `scopes_json`, expiry/revocation/last-used timestamps, creator, and audit timestamps. Plain tokens are returned once by an operator-only provisioning CLI and never logged.

The API creates `OptimizationPrincipal(tenant_id, subject, credential_id, agent_ids, scopes, actor_type="claude_code_mcp")`. MCP inputs omit `tenant_id`. Scopes are `iteration:start`, `iteration:read`, `candidate:request`, and `evaluation:rerun`. Human-only actions reject MCP principals with `403 {code: "human_approval_required"}`.

## 3. MCP tools

- `start_iteration(agent_id, fixed_suite_id, exploration_suite_id?, max_candidates?, allowed_change_types?, idempotency_key)`.
- `get_iteration_status(agent_id, iteration_id)`.
- `wait_for_iteration_update(agent_id, iteration_id, after_sequence, timeout_seconds=30)`.
- `list_iteration_candidates(agent_id, iteration_id, cursor?, limit=20)`.
- `compare_candidates(agent_id, iteration_id, candidate_ids)`.
- `get_iteration_report(agent_id, iteration_id, report_id?, format="summary")`.
- `get_iteration_trend(agent_id, limit=10)`.
- `request_alternative_candidate(agent_id, iteration_id, diagnosis_id, constraints, idempotency_key)`.
- `rerun_candidate_evaluation(agent_id, iteration_id, candidate_id, suite_type, idempotency_key)`.

All return structured JSON with stable errors. Wait results include compact events, `next_sequence`, `terminal`, stage/progress, and suggested next action, with a 30-second maximum.

## 4. Resources and prompts

Resource templates are `iteration://{agent_id}/{iteration_id}/status`, `/report`, `/candidates`, and `iteration://{agent_id}/trend/latest-10`.

`monitor_iteration` tells Claude to loop with the cursor until terminal, surface failures, and fetch the final report. `analyze_iteration_report` tells Claude to analyze composite/group/hard-gate/case-regression data while preserving recommendation precedence.

## 5. Monitoring, idempotency, audit

Claude Code owns the cursor. Each wait returns the next durable sequence, so reconnect is safe. Repetitive progress is aggregated and capped; full histories are explicit paginated reads. Claude Channel push is deferred and may later carry only high-value events while cursor wait remains authoritative.

All writes require caller idempotency keys and forward an MCP request ID. The API combines credential, action, and key. Audit events store sanitized arguments, targets, outcome, request ID, and subject; tokens, prompts, and document bodies are excluded.

## 6. Deployment

Add a `sales-agent-mcp` entry point. It receives `SALES_AGENT_API_URL`, bind host/port, and TLS/proxy configuration, but no database URL. Production exposes `/mcp` behind HTTPS. Health/readiness check API reachability.

Claude Code project config contains only the URL. Credentials use a user-local secret or `headersHelper`, never committed `.mcp.json` plaintext. Document `claude mcp add --transport http`, `/mcp` verification, and a fixture smoke test.

## 7. Failure behavior

- `401`: missing/expired/revoked/invalid token.
- `403 scope_denied`: missing allowed scope.
- `403 human_approval_required`: approval/publish/rollback attempted.
- `404`: absent or out-of-scope target.
- `409`: lifecycle/idempotency conflict.
- `429`: credential rate/concurrency limit.
- `503`: Observability API unavailable; preserve cursor and retry safety.

## 8. Verification and rollout

Contract tests assert exact tools and forbidden-name absence. API tests cover hashing, scopes, allowlists, ownership, human denial, idempotency, rate limits, and audit. Transport tests exercise initialization, tools, resources, and prompts against a fake API. The Claude Code smoke test registers the server, starts a fixture iteration, resumes monitoring, and reads the report.

Deploy authorization first, then adapter internally, then one tenant credential, then broader rollout. Disabling the process or revoking credentials rolls back MCP without affecting the underlying iteration system.
