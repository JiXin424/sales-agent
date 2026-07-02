# Claude Code MCP Iteration Monitoring

*Last updated: 2026-07-02*

## Overview

The `sales-agent-mcp` server exposes the knowledge optimization iteration
Observability API to Claude Code through Model Context Protocol (MCP).
Claude Code can start, monitor, and analyze iterations without accessing
the database or invoking human-only actions (approve, publish, rollback).

## Architecture

```text
Claude Code ─── MCP Streamable HTTP ─── sales-agent-mcp ─── HTTPS ─── Observability API ─── PostgreSQL
```

The MCP server is a thin adapter. It has no database access and owns no
iteration state. Every call is forwarded to the Observability API with
the caller's bearer token.

## Prerequisites

- Observability API deployed and reachable (migration `0008` applied).
- MCP server host accessible from Claude Code (HTTPS recommended).
- Operator-issued tenant-scoped credential.

## Credential Management (Operator-Only)

```bash
# Create a credential
sales-agent optimization mcp-token-create \
  --tenant t1 \
  --subject "claude-prod-monitor" \
  --agents "agent-a,agent-b" \
  --scopes "iteration:start,iteration:read,candidate:request,evaluation:rerun" \
  --expires-days 90

# Revoke a credential
sales-agent optimization mcp-token-revoke --credential <id> --yes
```

**Security notes:**
- Only scrypt hashes are stored. The plaintext token is shown once.
- Tokens prefix with `saopt_` (44-character total).
- Never commit tokens to `.mcp.json` or version control.
- Use `headersHelper` or environment variables for Claude Code registration.

## Claude Code Registration

```bash
# Register the MCP server
claude mcp add --transport http --scope local sales-agent-iteration \
  https://sales-agent.example.internal/mcp \
  --header "Authorization: Bearer ${SALES_AGENT_MCP_TOKEN}"

# Verify
claude mcp list
```

## Tools (9)

| Tool | Scope Required | Description |
|------|---------------|-------------|
| `start_iteration` | `iteration:start` | Start a new optimization iteration |
| `get_iteration_status` | `iteration:read` | Read current iteration state |
| `wait_for_iteration_update` | `iteration:read` | Long-poll for new events (cursor-based) |
| `list_iteration_candidates` | `iteration:read` | List candidates |
| `compare_candidates` | `iteration:read` | Compare candidate reports |
| `get_iteration_report` | `iteration:read` | Fetch effect report (summary/json/md/html/csv) |
| `get_iteration_trend` | `iteration:read` | Latest 10 final report trends |
| `request_alternative_candidate` | `candidate:request` | Request alternative candidate with constraints |
| `rerun_candidate_evaluation` | `evaluation:rerun` | Rerun targeted or fixed eval suite |

## Permission Matrix

| Capability | MCP |
|---|---|
| Start iteration | ✅ |
| Read status / events / reports / trends | ✅ |
| Request alternative candidate | ✅ |
| Rerun evaluation | ✅ |
| Approve candidate / iteration | ❌ `403 human_approval_required` |
| Publish release | ❌ `403 human_approval_required` |
| Roll back release | ❌ `403 human_approval_required` |
| Access another tenant or unlisted Agent | ❌ `404` |

## Resources (4)

| URI Template | Description |
|---|---|
| `iteration://{agent_id}/{iteration_id}/status` | Current iteration status text |
| `iteration://{agent_id}/{iteration_id}/report` | Report summary text |
| `iteration://{agent_id}/{iteration_id}/candidates` | Candidate count text |
| `iteration://{agent_id}/trend/latest-10` | Latest 10 trend lines |

## Prompts (2)

### `monitor_iteration`
Monitors an iteration from start to completion. Loops with cursor-based
waits, surfaces failures immediately, and fetches the final report.

### `analyze_iteration_report`
Analyzes a completed iteration's effect report: composite index, metric
groups, hard gates, per-case regressions, and recommendation.

## Error Codes

| HTTP | Code | Meaning |
|------|------|---------|
| 401 | `invalid_token` | Missing, expired, or revoked credential |
| 403 | `scope_denied` | Credential lacks required scope |
| 403 | `human_approval_required` | Approve/publish/rollback requires operator |
| 404 | `not_found` | Target not found or out of allowlist |
| 409 | `conflict` | Lifecycle or idempotency conflict |
| 429 | `rate_limited` | Rate/concurrency limit |
| 503 | `api_unavailable` | Observability API unreachable |

## Monitoring Pattern (Cursor-Based)

```python
# Claude Code internally loops:
cursor = 0
while True:
    result = wait_for_iteration_update(
        agent_id="a1", iteration_id="i1",
        after_sequence=cursor, timeout_seconds=30,
    )
    for event in result["events"]:
        print(f"[{event['seq']}] {event['type']}: {event['message']}")
    cursor = result["next_sequence"]
    if result["terminal"]:
        break

# Fetch final report
report = get_iteration_report(agent_id="a1", iteration_id="i1")
```

## Reconnection Safety

- Claude Code owns the cursor. On disconnect, resume with `after_sequence=cursor`.
- Wait returns only unseen events; no duplicates.
- Heartbeat comments every 15 seconds on SSE transport.

## Deployment

### Service (systemd)

```ini
[Unit]
Description=Sales Agent Iteration MCP Server
After=network.target

[Service]
Type=simple
User=sales-agent
EnvironmentFile=/etc/sales-agent/mcp.env
ExecStart=sales-agent-mcp --host 127.0.0.1 --port 3001
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Environment File (`/etc/sales-agent/mcp.env`)

```bash
SALES_AGENT_API_URL=http://127.0.0.1:8000
```

### Nginx Reverse Proxy

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:3001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 120s;
    proxy_buffering off;
    # Rate limiting
    limit_req zone=mcp burst=10 nodelay;
    limit_conn mcp_conn 5;
}
```

## Smoke Test

```bash
# 1. Verify server starts
SALES_AGENT_API_URL=http://localhost:8000 sales-agent-mcp --help

# 2. Create fixture credential
sales-agent optimization mcp-token-create --tenant t1 --subject "smoke-test" --agents "a1"

# 3. Register with Claude Code
claude mcp add --transport http --scope local sales-agent-iteration http://localhost:3001/mcp \
  --header "Authorization: Bearer $TOKEN"

# 4. List tools (expect 9)
claude mcp list

# 5. Start a fixture iteration
# (via Claude Code): start_iteration agent_id="a1" fixed_suite_id="suite1"

# 6. Wait and verify
# wait_for_iteration_update → returns events
# get_iteration_report → returns effect report

# 7. Attempt publish (expect 403)
# publish attempt → 403 human_approval_required

# 8. Cleanup
sales-agent optimization mcp-token-revoke --credential <id> --yes
claude mcp remove sales-agent-iteration
```

## Rotation

```bash
# 1. Create new credential
sales-agent optimization mcp-token-create ...

# 2. Update Claude Code registration
claude mcp remove sales-agent-iteration
claude mcp add ... --header "Authorization: Bearer $NEW_TOKEN"

# 3. Revoke old credential
sales-agent optimization mcp-token-revoke --credential <old-id> --yes
```

## Verification Commands

```bash
# Backend tests
pytest tests/unit/mcp_server tests/unit/security tests/unit/optimization -q

# Migration check
alembic upgrade head && alembic current
# Expected: 0009_optimization_api_credentials

# Task validation
python3 ./.trellis/scripts/task.py validate 07-02-claude-code-mcp-monitoring
```
