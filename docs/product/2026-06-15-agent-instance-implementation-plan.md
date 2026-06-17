# Agent Instance — Implementation Plan

**Spec:** `2026-06-15-agent-instance-one-to-one-clone-spec.md`
**Created:** 2026-06-15
**Status:** planning

## Codebase facts discovered during exploration

- Backend: FastAPI + SQLAlchemy 2 async + asyncpg + pgvector. App entry `src/sales_agent/main.py`.
- **No Alembic.** Schema created via `Base.metadata.create_all` (dev + test conftest re-creates tables each run). Prod alterations are hand-written `scripts/add_*.sql`. → New tables auto-create via `create_all`; adding nullable `agent_id` to existing tables needs a `scripts/add_agent_id_columns.sql` migration + a Python backfill util.
- **Deployment is dedicated-mode** (`TenantRuntime.from_current_env()`, one instance = one tenant, `check_tenant_match` enforces it). Agent layer sits *above* tenant and must preserve this guard.
- Tables already store `tenant_id`; JSON is stored as `*_json` Text columns. IDs are 16-hex (`generate_id`). All models extend `TimestampMixin` + `Base`.
- Chat pipeline: `api/routes/agent.py` → `ChatPipeline.execute(tenant_id, user_id, message, ...)`. Retrieval forces `tenant_id`. Prompt resolution: `PromptRegistry.resolve(tenant_id, task_type)` → tenant active version, else Python default.
- Frontend: React Router v6 + antd + @tanstack/react-query. `TenantContext` persists tenant to localStorage and gates `AppLayout`. Static `Sidebar`. API layer in `console/src/api/*` wraps `apiGet/apiPost/...`.
- DingTalk: `integrations/dingtalk/processor.py::handle_dingtalk_event(...)` calls `ChatPipeline` with the instance tenant. Channel→agent resolution will be added here.
- Tests: `PYTHONPATH=src python3 -m pytest`. Real PG at `sales-agent-db:5432`. Baseline = 410 passed, 4 pre-existing errors (conftest missing `Feedback`/`AgentRun` imports).
- **No git repo** → no per-step commits/worktrees; rely on tests + verification.

## Resolved product decisions (spec "Open Product Decisions")

1. Cloned knowledge → **reference** within same tenant, **empty** cross-tenant.
2. Multiple active Agents per tenant of same type → **yes**.
3. Prompt-set copy → create a new `agent_prompt_set` whose `task_prompt_versions_json` maps task→`prompt_version_id`; "copy" duplicates the referenced `PromptVersion` rows into new draft rows (new ids) so the clone can edit independently; "reference" reuses the source version ids.
4. `/dashboard` → redirect normal users to `/agents`.

## Phase 1 — Data model + migration

**Acceptance:** New Agent tables exist; runtime/ops tables carry nullable `agent_id`; each existing tenant gets a default Agent and its rows are backfilled; ownership tests pass; existing suite still green.

Tasks:
1. New models (`src/sales_agent/models/`): `agent.py` (`Agent`), `agent_prompt_set.py`, `agent_knowledge_scope.py`, `agent_risk_policy.py`, `agent_channel_config.py`, `agent_clone_manifest.py`. Register in `models/__init__.py`.
2. Add nullable indexed `agent_id: Text` to: `Conversation`, `RetrievalLog`, `ConversationMessage`, `ConversationSummary`, `AgentRun`, `AgentRunStep`, `Feedback`, `ReviewItem`, `KnowledgeGap`, `ModelCallLog`, `EvalRun`, `Alert`, `PilotReport`, plus `Document`/`SourceFile`/`DocumentChunk`/`EvalSuite`/`PromptVersion` (knowledge & prompt scope). Keep all `tenant_id`.
3. Migration script `scripts/add_agent_id_columns.sql` (`ADD COLUMN IF NOT EXISTS agent_id TEXT`, indexes) for prod.
4. Backfill util `services/agent_migration.py`: `ensure_default_agents(db)` — for each tenant with no Agent, create default `{name} Sales Agent` (status `active`, `model_config_ref` = "runtime", prompt_set/knowledge_scope default modes), set as that tenant's default, then `UPDATE ... SET agent_id = default WHERE agent_id IS NULL` across all runtime/ops tables for that tenant. Idempotent. Wired into `init_db()` (post-`create_all`) + a CLI command.
5. Tests: `tests/unit/test_agent_migration.py` (default-agent creation, idempotency, backfill correctness, no cross-tenant leakage).

## Phase 2 — Agent APIs + clone service + readiness

**Acceptance:** Agent CRUD/status, clone, readiness, manifest endpoints work; clone copies prompt/risk/eval, never copies secrets/runtime; tests cover each.

Tasks:
1. Schemas (`api/schemas.py`): Agent create/detail/list/update; CloneRequest (options); CloneManifestResponse; ReadinessResponse; AgentContext wrappers.
2. New router `api/routes/agents.py` (`prefix="/agents"`): `GET /agents`, `POST /agents`, `GET/PATCH /agents/{id}`, `POST .../clone`, `GET .../clone-manifest`, `GET .../readiness`, `POST .../activate|pause|archive`, plus agent-scoped wrappers (`/conversations`, `/runs/{run_id}`, `/knowledge/documents`, `/prompts`, `/feedback`, `/pilot/*`). Internally call existing services filtered by `(tenant_id, agent_id)`. Enforce `agent.tenant_id` belongs to resolved/instance tenant.
3. `services/agent_service.py`: CRUD + status transitions (draft→active→paused→archived) + ownership guard.
4. `services/agent_clone_service.py`: executes clone options (metadata/prompt/risk/knowledge/eval/channel/model), writes `agent_clone_manifests` (copied/referenced/reset/skipped). **Never** copies `*_secret`, webhook credentials, or any runtime table.
5. `services/agent_readiness_service.py`: checks model/prompt/knowledge/risk/channel/eval → list of `{check, passed, required, reason}`; activation blocked unless all required pass or waiver recorded (`feature_flags_json` `activation_waivers`).
6. Wire router into `main.py`.
7. Tests: `tests/integration/test_agents_api.py` (CRUD, status transitions, ownership, clone options × no-secret × no-runtime, readiness gate, agent-scoped filters, backward compat).

## Phase 3 — Runtime resolution

**Acceptance:** `/agent/chat` routes by `agent_id`; tenant-only calls fall back to tenant default Agent; retrieval & prompt respect agent scope; DingTalk binds to agent; isolation tests pass.

Tasks:
1. `ChatRequest` gains optional `agent_id`. `ChatPipeline.execute(..., agent_id=None)`: resolve agent (validate belongs to tenant; default-Agent fallback when None); thread `agent_id` into `Conversation`, `AgentRun`, logs, `ModelCallLog`.
2. `AgentResolver` (`services/agent_resolver.py`): `resolve(tenant_id, agent_id=None)` → returns agent + its prompt_set/knowledge_scope/risk_policy refs; default fallback.
3. `PromptRegistry.resolve(agent_id, task_type)`: agent prompt_set version first → tenant default → Python constant fallback.
4. `Retriever.retrieve`: accept agent knowledge_scope; for `tenant_all` keep current; for `document_subset`/`source_file_subset` add `document_id IN (...)` filter on top of tenant filter.
5. Risk policy: load per-agent `agent_risk_policies.rules_json` when present, else tenant config.
6. DingTalk: `processor.py` resolves `agent_id` from channel binding (`agent_channel_configs`); pass to pipeline.
7. Tests: extend `test_*` for agent-scoped prompt/retrieval divergence between two agents under one tenant, default-Agent fallback, archived/paused rejection.

## Phase 4 — Frontend Agent shell

**Acceptance:** `/agents` list + agent-scoped routes work without tenant dropdown; existing pages re-wired to `agentId`.

Tasks:
1. `api/agents.ts` (CRUD/clone/readiness/agent-scoped wrappers) + types.
2. `context/AgentContext.tsx`: active agent, resolved tenant, status, feature flags; persist; invalidate react-query on switch. Keep `TenantContext` for `/agents` list filter + aggregate admin.
3. `AgentSwitcher` + `AgentLayout` (sidebar scoped to open agent, status badge). `App.tsx`: add `/agents`, `/agents/new`, `/agents/:agentId/{overview,setup,knowledge,prompts,channels,conversations,traces/:runId,quality,eval,alerts,reports,settings}`. `/dashboard` → redirect to `/agents`.
4. Agent List page (table: name/type/status/tenant/channel/knowledge/prompt/quality/actions: open/clone/pause/archive/create).
5. Agent Overview + Setup (readiness checklist) + Settings (metadata/manifest/feature flags).
6. Re-wire existing pages to read `agentId` param + call agent-scoped APIs.

## Phase 5 — Clone wizard + activation UI

**Acceptance:** Create + clone wizard produce draft agents; activation gated by readiness; frontend tests cover flows.

Tasks:
1. Create Agent flow (template/tenant/name/model/prompt/knowledge-strategy/risk/channel-mode) → draft → setup.
2. Clone Wizard (source summary, editable target, copy/reference/reset choices per resource table, secret-exclusion warning, manifest preview) → `POST /clone` → redirect to new agent settings.
3. Activate/Pause/Archive with confirmations + actionable readiness reasons.
4. Frontend tests (`console/src/tests`) for create/open/clone/activate.

## Risks & mitigations

- **No Alembic** → provide both model columns and `scripts/add_agent_id_columns.sql`; backfill util idempotent and guarded.
- **Dedicated-mode guard** → agent.tenant_id must equal `TenantRuntime.tenant_id`; preserve `check_tenant_match`.
- **Backward compat** → agent_id optional everywhere; `agent_migration.ensure_default_agents` makes tenant-only calls keep working via default Agent.
- **Secret leakage in clone** → clone service only ever reads non-secret config; manifest asserts no secret fields; tests assert.
- **Scope** → backend (Phases 1–3) is the verifiable core; deliver it fully first, then frontend.

## Verification gate (per phase)

Run `PYTHONPATH=src python3 -m pytest tests/ -q` after each phase → must stay green and grow new passing tests. Frontend: `cd console && npm test` (vitest) after Phases 4–5.
