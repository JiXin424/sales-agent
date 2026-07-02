# Claude Code MCP Iteration Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude Code securely start, monitor, and analyze knowledge optimization iterations through MCP without granting approval, publish, or rollback authority.

**Architecture:** A separate Streamable HTTP MCP process forwards typed calls to the completed Observability API. The API authenticates hashed tenant-scoped credentials and enforces scopes/Agent ownership; the adapter has no database access and owns no iteration state.

**Tech Stack:** Python 3.12, official MCP Python SDK/FastMCP `>=1.27,<2`, HTTPX, FastAPI, SQLAlchemy asyncio, Alembic, PostgreSQL, pytest, Claude Code remote HTTP MCP.

---

## Dependency gate

Do not begin until `.trellis/tasks/07-02-iteration-observability-reports` acceptance tests pass and migration `0008_iteration_observability_reports` is deployed.

## File map

- `src/sales_agent/models/optimization_auth.py`: scoped credential and command audit models.
- `src/sales_agent/migrations/versions/0009_optimization_api_credentials.py`: auth schema.
- `src/sales_agent/security/optimization_credentials.py`: token issue/hash/verify/revoke.
- `src/sales_agent/api/optimization_auth.py`: principal dependency and policy checks.
- `src/sales_agent/api/routes/optimization.py`: protected command endpoints and human-only denials.
- `src/sales_agent/mcp_server/api_client.py`: typed Observability HTTP client.
- `src/sales_agent/mcp_server/server.py`: FastMCP tools/resources/prompts.
- `src/sales_agent/mcp_server/main.py`: Streamable HTTP process configuration.
- `src/sales_agent/cli_optimization.py`: operator token provisioning/revocation.
- `docs/claude-code-iteration-mcp.md`: setup, permissions, and smoke test.

## Task 1: Persist and verify scoped API credentials

**Files:**
- Create: `src/sales_agent/models/optimization_auth.py`
- Create: `src/sales_agent/migrations/versions/0009_optimization_api_credentials.py`
- Create: `src/sales_agent/security/optimization_credentials.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/security/test_optimization_credentials.py`
- Test: `tests/unit/optimization/test_mcp_auth_models.py`

- [ ] **Step 1: Write failing hash, revoke, expiry, and metadata tests**

```python
def test_issue_stores_no_plaintext_token():
    issued = issue_token()
    assert issued.plaintext.startswith("saopt_")
    assert issued.plaintext not in issued.encoded_hash
    assert verify_token(issued.plaintext, issued.encoded_hash)

@pytest.mark.asyncio
async def test_revoked_credential_is_rejected(db_session, credential):
    credential.revoked_at = utcnow()
    with pytest.raises(InvalidOptimizationCredential):
        await OptimizationCredentialService(db_session).authenticate(credential.plaintext)
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/security/test_optimization_credentials.py tests/unit/optimization/test_mcp_auth_models.py -q`

Expected: FAIL because auth modules and tables do not exist.

- [ ] **Step 3: Implement models and migration**

Create `OptimizationApiCredential` with tenant, subject, token prefix, scrypt hash, allowed Agent/scopes JSON, expiry/revocation/last-used, and creator. Create `OptimizationCommandAudit` with tenant, credential/subject, action, target IDs, request/idempotency keys, sanitized input JSON, outcome/error, and timestamp. All indexes begin with tenant where business-scoped.

Migration `0009_optimization_api_credentials` depends on `0008_iteration_observability_reports`.

- [ ] **Step 4: Implement standard-library scrypt tokens**

```python
def hash_token(token: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(token.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"

def verify_token(token: str, encoded: str) -> bool:
    scheme, n, r, p, salt_hex, expected_hex = encoded.split("$")
    actual = hashlib.scrypt(token.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
    return scheme == "scrypt" and hmac.compare_digest(actual.hex(), expected_hex)
```

Generate 32 random bytes, encode URL-safe, prefix `saopt_`, and use a lookup prefix that is not sufficient to authenticate.

- [ ] **Step 5: Verify tests and migration**

Run: `pytest tests/unit/security/test_optimization_credentials.py tests/unit/optimization/test_mcp_auth_models.py tests/unit/test_entrypoint_migration.py -q && alembic upgrade head && alembic current`

Expected: PASS and Alembic reports `0009_optimization_api_credentials`.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/models src/sales_agent/security src/sales_agent/migrations/versions/0009_optimization_api_credentials.py tests/unit
git commit -m "feat: add scoped optimization API credentials"
```

## Task 2: Enforce principal scopes and human-only actions in the API

**Files:**
- Create: `src/sales_agent/api/optimization_auth.py`
- Modify: `src/sales_agent/api/deps.py`
- Modify: `src/sales_agent/api/routes/optimization.py`
- Modify: `src/sales_agent/api/optimization_schemas.py`
- Test: `tests/integration/test_optimization_api_authorization.py`

- [ ] **Step 1: Write ownership, scope, and denial tests**

```python
@pytest.mark.parametrize("path", [
    "/agents/a1/optimization/iterations/i1/approve",
    "/agents/a1/optimization/candidates/c1/publish",
    "/agents/a1/optimization/releases/rollback",
])
async def test_mcp_principal_cannot_perform_human_actions(client, mcp_headers, path):
    response = await client.post(path, headers=mcp_headers, json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "human_approval_required"
```

Also assert a read-only credential cannot start, an Agent allowlist mismatch is 404, another tenant is 404, and valid scopes work.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/integration/test_optimization_api_authorization.py -q`

Expected: FAIL because routes do not authenticate scoped principals.

- [ ] **Step 3: Implement principal and policy helpers**

```python
@dataclass(frozen=True)
class OptimizationPrincipal:
    tenant_id: str
    subject: str
    credential_id: str
    agent_ids: frozenset[str]
    scopes: frozenset[str]
    actor_type: str = "claude_code_mcp"

def require_scope(principal: OptimizationPrincipal, scope: str) -> None:
    if scope not in principal.scopes:
        raise OptimizationAuthorizationError("scope_denied", status_code=403)
```

Parse Bearer tokens, authenticate, update last-used without logging token, and provide an explicit operator compatibility principal only to existing Web/CLI authentication paths. Do not accept tenant from request bodies.

- [ ] **Step 4: Protect all MCP-reachable operations**

Status/events/reports/trends require `iteration:read`; start requires `iteration:start`; alternative candidate requires `candidate:request`; rerun requires `evaluation:rerun`. Approval/publish/rollback call `require_human_principal()` before loading the target to ensure structured denial for MCP identities.

- [ ] **Step 5: Run authorization tests**

Run: `pytest tests/integration/test_optimization_api_authorization.py tests/integration/test_optimization_events_api.py tests/integration/test_optimization_reports_api.py -q`

Expected: PASS without regressing existing operator routes.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/api tests/integration/test_optimization_api_authorization.py
git commit -m "feat: enforce optimization API principals and scopes"
```

## Task 3: Add idempotent alternative-candidate and rerun commands

**Files:**
- Modify: `src/sales_agent/api/routes/optimization.py`
- Modify: `src/sales_agent/api/optimization_schemas.py`
- Modify: `src/sales_agent/optimization/tools.py`
- Modify: `src/sales_agent/optimization/worker.py`
- Test: `tests/integration/test_optimization_mcp_commands.py`

- [ ] **Step 1: Write command idempotency and audit tests**

Call each endpoint twice with the same `Idempotency-Key` and assert one candidate/job and two responses with the same command result. Assert the audit row contains subject/action/result but not evidence text, token, or document content. Reject suite values outside `targeted` and `fixed`.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/integration/test_optimization_mcp_commands.py -q`

Expected: FAIL because the command endpoints do not exist.

- [ ] **Step 3: Implement constrained command schemas**

```python
class AlternativeCandidateRequest(BaseModel):
    diagnosis_id: str
    constraints: dict[str, str | int | float | bool] = Field(default_factory=dict)

class RerunCandidateEvalRequest(BaseModel):
    suite_type: Literal["targeted", "fixed"]
```

Require `Idempotency-Key` length 8–128. Alternative requests preserve the diagnosis change domain and candidate limit; reruns use the candidate's pinned sandbox manifest. Neither endpoint changes approval/publication state.

- [ ] **Step 4: Implement audit in the command transaction**

Create/reuse the candidate or job and append `OptimizationCommandAudit` plus an iteration event in one transaction. On duplicate key for the same credential/action, return the recorded target. On the same key with different normalized input hash, return 409.

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_optimization_mcp_commands.py tests/unit/optimization -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/api src/sales_agent/optimization tests/integration/test_optimization_mcp_commands.py
git commit -m "feat: add audited optimization candidate commands"
```

## Task 4: Build the typed Observability API client

**Files:**
- Modify: `pyproject.toml`
- Create: `src/sales_agent/mcp_server/__init__.py`
- Create: `src/sales_agent/mcp_server/types.py`
- Create: `src/sales_agent/mcp_server/api_client.py`
- Test: `tests/unit/mcp_server/test_api_client.py`

- [ ] **Step 1: Add the stable MCP dependency and failing client tests**

Add `mcp>=1.27,<2` to project dependencies. With HTTPX MockTransport, test Authorization/request/idempotency headers, URL escaping, timeout mapping, compact event parsing, 401/403/404/409/429/503 error mapping, and cursor preservation on 503.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/mcp_server/test_api_client.py -q`

Expected: FAIL because the client package does not exist.

- [ ] **Step 3: Implement the API client**

```python
class ObservabilityApiClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None): ...

    async def wait_for_update(self, agent_id: str, iteration_id: str, after_sequence: int, timeout_seconds: int) -> EventPage:
        return await self._request(
            "GET",
            f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}/events/wait",
            params={"after_sequence": after_sequence, "timeout_seconds": min(max(timeout_seconds, 1), 30)},
            timeout=timeout_seconds + 5,
        )
```

Implement one method per MCP capability. Never accept full URLs from tool arguments. Close the HTTPX client during MCP lifespan shutdown.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/mcp_server/test_api_client.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/sales_agent/mcp_server tests/unit/mcp_server/test_api_client.py
git commit -m "feat: add typed optimization observability client"
```

## Task 5: Expose the MCP tools, resources, and prompts

**Files:**
- Create: `src/sales_agent/mcp_server/server.py`
- Test: `tests/unit/mcp_server/test_contract.py`
- Test: `tests/integration/test_mcp_transport.py`

- [ ] **Step 1: Write the exact contract tests**

```python
ALLOWED_TOOLS = {
    "start_iteration", "get_iteration_status", "wait_for_iteration_update",
    "list_iteration_candidates", "compare_candidates", "get_iteration_report",
    "get_iteration_trend", "request_alternative_candidate", "rerun_candidate_evaluation",
}
FORBIDDEN_FRAGMENTS = {"approve", "publish", "rollback", "sql", "url", "file"}

def test_tool_surface_is_allowlisted(mcp_server):
    names = set(list_tool_names(mcp_server))
    assert names == ALLOWED_TOOLS
    assert not any(fragment in name for name in names for fragment in FORBIDDEN_FRAGMENTS)
```

Add initialization/tool/resource/prompt tests over Streamable HTTP with a fake API client.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/mcp_server/test_contract.py tests/integration/test_mcp_transport.py -q`

Expected: FAIL because the MCP server is absent.

- [ ] **Step 3: Build FastMCP with concise instructions and typed outputs**

```python
mcp = FastMCP(
    "sales-agent-iteration",
    instructions=(
        "Start, monitor, and analyze tenant-scoped knowledge optimization iterations. "
        "This server cannot approve, publish, or roll back releases."
    ),
)

@mcp.tool()
async def wait_for_iteration_update(agent_id: str, iteration_id: str, after_sequence: int = 0, timeout_seconds: int = 30) -> WaitResult:
    return await client().wait_for_update(agent_id, iteration_id, after_sequence, timeout_seconds)
```

Register exactly the PRD tools. Mutating tool schemas require idempotency keys. Clamp lists and omit full case/evidence bodies from summaries.

- [ ] **Step 4: Register resources and prompts**

Resource handlers read via the API client. Prompt text explicitly stops on terminal state, preserves the latest cursor, surfaces failed/hard-gate events immediately, and never asks a forbidden tool to publish.

- [ ] **Step 5: Run contract and transport tests**

Run: `pytest tests/unit/mcp_server tests/integration/test_mcp_transport.py -q`

Expected: PASS; discovered tool set exactly matches the allowlist.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/mcp_server/server.py tests/unit/mcp_server tests/integration/test_mcp_transport.py
git commit -m "feat: expose iteration monitoring over MCP"
```

## Task 6: Add process entry point, health checks, and credential CLI

**Files:**
- Create: `src/sales_agent/mcp_server/main.py`
- Modify: `pyproject.toml`
- Modify: `src/sales_agent/cli_optimization.py`
- Test: `tests/unit/mcp_server/test_main.py`
- Test: `tests/unit/test_optimization_cli.py`

- [ ] **Step 1: Write configuration and provisioning tests**

Assert startup fails without `SALES_AGENT_API_URL`; bind defaults to loopback; token is read from each inbound Authorization header rather than one process-global tenant token; token-create prints plaintext once and JSON metadata without its hash; token-revoke is operator-confirmed.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/mcp_server/test_main.py tests/unit/test_optimization_cli.py -q`

Expected: FAIL on missing entry point and token commands.

- [ ] **Step 3: Implement Streamable HTTP startup**

Add `sales-agent-mcp = "sales_agent.mcp_server.main:main"`. Configure host, port, `/mcp`, log level, trusted proxy behavior, and readiness. Pass the inbound bearer token into a request-scoped API client; never log headers.

- [ ] **Step 4: Implement operator credential commands**

Add `mcp-token-create --tenant --subject --agent --scope --expires-days` and `mcp-token-revoke --credential --yes`. Validate scopes against the four-value allowlist and Agent ownership before issuance. Print the token once to stderr/stdout with a warning that it cannot be recovered.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/mcp_server/test_main.py tests/unit/test_optimization_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/sales_agent/mcp_server/main.py src/sales_agent/cli_optimization.py tests/unit
git commit -m "feat: run and provision the iteration MCP server"
```

## Task 7: Document and verify Claude Code integration

**Files:**
- Create: `docs/claude-code-iteration-mcp.md`
- Create: `deploy/systemd/sales-agent-mcp.service`
- Create: `deploy/nginx/sales-agent-mcp.conf.example`
- Modify: `.trellis/tasks/07-02-claude-code-mcp-monitoring/check.jsonl`

- [ ] **Step 1: Document secure installation**

Include service environment, HTTPS reverse proxy, token issuance/revocation, Agent/scopes, and this user-local registration example without a real secret:

```bash
claude mcp add --transport http --scope local sales-agent-iteration https://sales-agent.example.internal/mcp \
  --header "Authorization: Bearer ${SALES_AGENT_MCP_TOKEN}"
claude mcp list
```

Prefer `headersHelper` or a user-local environment. State that project `.mcp.json` must not contain plaintext credentials. Document `/mcp` status inspection, monitoring prompt, report prompt, permission matrix, errors, rotation, and removal.

- [ ] **Step 2: Add service and proxy definitions**

Run MCP as an unprivileged user bound to loopback, restart on failure, load secrets from a root-readable environment file, expose only `/mcp` and health paths, preserve Authorization, apply request/concurrency limits, and enforce TLS at the proxy.

- [ ] **Step 3: Run backend and MCP verification**

Run: `pytest tests/unit/mcp_server tests/unit/security/test_optimization_credentials.py tests/integration/test_optimization_api_authorization.py tests/integration/test_optimization_mcp_commands.py tests/integration/test_mcp_transport.py -q`

Expected: PASS.

- [ ] **Step 4: Run migration and transport smoke tests**

Run: `alembic upgrade head && alembic current && sales-agent-mcp --help`

Expected: Alembic at `0009_optimization_api_credentials`; MCP command prints help without importing database models.

- [ ] **Step 5: Perform the documented Claude Code fixture smoke test**

Register a fixture credential, run `claude mcp list`, invoke status, start a fixture iteration with a unique idempotency key, wait through one disconnect/reconnect, fetch the final report, and attempt a publish request against the API. Expected: monitoring/report succeed, sequence resumes without duplicates, and publish returns `403 human_approval_required`.

- [ ] **Step 6: Validate task and commit**

Run: `python3 ./.trellis/scripts/task.py validate 07-02-claude-code-mcp-monitoring && git diff --check`

Expected: task and diff validation PASS.

```bash
git add docs/claude-code-iteration-mcp.md deploy .trellis/tasks/07-02-claude-code-mcp-monitoring
git commit -m "docs: add Claude Code iteration MCP operations"
```
