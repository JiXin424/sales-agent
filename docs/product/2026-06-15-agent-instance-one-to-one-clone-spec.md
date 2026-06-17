# Agent Instance One-to-One Console and Clone - Specification

**Created:** 2026-06-15
**Ambiguity score:** 0.16 (gate: <= 0.20)
**Requirements:** 12 locked

## Goal

Refactor the product model from a tenant-selected shared admin console into a product-level one-to-one Agent experience: every Agent has its own management page, configuration scope, runtime binding, and clone flow. This enables operators to quickly create new Agents from an existing working Agent without manually rebuilding prompts, knowledge settings, risk rules, eval suites, and channel configuration.

## Background

The current console is tenant-centric. `TenantSelector` stores one active `tenant_id` in localStorage, and pages such as dashboard, knowledge, prompts, feedback, eval, alerts, and reports request tenant-scoped APIs. This is usable for pilot operations, but product users think in terms of Agents, not raw tenants.

Current effective model:

```text
One shared console -> select tenant -> manage that tenant's sales Agent data
```

Target product model:

```text
Agent list -> open one Agent -> manage exactly that Agent's page and capabilities
```

For rapid replication, an Agent must become a first-class entity that packages tenant binding, model config, prompt set, knowledge base scope, risk policy, eval suite, DingTalk/channel settings, and operational status.

## Product Definition

### Agent Instance

An Agent Instance is the product object users create, open, configure, activate, pause, and clone.

Minimum fields:

- `agent_id`: stable unique ID.
- `tenant_id`: owning tenant or deployment scope.
- `name`: display name, for example `泰山销售陪跑 Agent`.
- `agent_type`: product template type, initially `sales_assistant`.
- `description`: short internal description.
- `status`: `draft`, `active`, `paused`, `archived`.
- `source_agent_id`: original Agent when cloned.
- `model_config_ref`: model/runtime configuration reference.
- `prompt_set_ref`: active prompt set or prompt version mapping.
- `knowledge_scope_ref`: documents/source files/chunks used by this Agent.
- `risk_policy_ref`: risk rules and output constraints.
- `eval_suite_ref`: evaluation suite used before activation.
- `channel_config_refs`: DingTalk or future channel bindings.
- `created_by`, `created_at`, `updated_at`, `activated_at`.

### One-to-One Page

Each Agent gets one stable management entry:

```text
/agents/:agentId/overview
/agents/:agentId/knowledge
/agents/:agentId/prompts
/agents/:agentId/channels
/agents/:agentId/eval
/agents/:agentId/conversations
/agents/:agentId/quality
/agents/:agentId/reports
/agents/:agentId/settings
```

The user should not need to select a tenant after opening an Agent. The active `agent_id` determines tenant scope and all API calls.

### Clone

Clone means creating a new Agent from an existing Agent's product configuration. It must be explicit which assets are copied, referenced, reset, or excluded.

Default clone behavior:

- Copy Agent metadata as editable draft values.
- Copy prompt versions into a new prompt set, preserving active version choices.
- Copy risk policy rules.
- Copy eval suite definitions.
- Copy UI/page configuration and enabled feature flags.
- Reference or copy knowledge documents based on user choice.
- Create channel configuration shell, but do not copy secrets or active webhook credentials.
- Reset runtime data: conversations, feedback, traces, alerts, reports, model call logs, eval run results.
- New Agent starts as `draft` until readiness checks pass.

## Requirements

1. **Agent as first-class product entity**
   - Current: Tenant is the primary console context.
   - Target: Agent Instance exists as a persisted entity and is the primary console context.
   - Acceptance: API can create, list, read, update, pause, activate, archive, and clone Agents.

2. **Agent list page**
   - Current: No Agent list; console starts from tenant selector.
   - Target: `/agents` shows all Agents with name, type, status, tenant, channel state, knowledge status, prompt status, latest quality status, and actions.
   - Acceptance: A user can open, clone, pause, archive, or create an Agent from the list.

3. **One Agent, one management page**
   - Current: Pages are global routes using selected tenant.
   - Target: Agent-specific routes include `agentId`; pages derive tenant and scope from Agent context.
   - Acceptance: Opening `/agents/{agentId}/knowledge` only shows and mutates that Agent's configured knowledge scope.

4. **Agent context replaces tenant selector for Agent pages**
   - Current: `TenantContext` stores selected tenant in localStorage.
   - Target: `AgentContext` stores selected/open Agent and exposes resolved tenant, status, feature flags, and permissions.
   - Acceptance: Agent pages do not require the top tenant dropdown; changing Agent clears stale queries.

5. **Agent create flow**
   - Target: Create flow supports selecting template type, tenant, display name, model config, initial prompt set, knowledge scope strategy, risk policy, and channel setup mode.
   - Acceptance: Creating an Agent produces a `draft` Agent and redirects to its setup/readiness page.

6. **Agent clone flow**
   - Target: Clone wizard supports choosing which resources to copy or reference.
   - Acceptance: Cloning from an active Agent creates a draft Agent with copied prompt/risk/eval configuration, no runtime history, no copied secrets, and an auditable clone manifest.

7. **Clone manifest and auditability**
   - Target: Every clone stores a manifest explaining what was copied, referenced, reset, skipped, and why.
   - Acceptance: The new Agent settings page shows source Agent, clone time, selected clone options, and resources created.

8. **Agent-scoped knowledge**
   - Current: Knowledge is tenant-scoped.
   - Target: Agent can use either tenant-wide knowledge or an Agent-specific document subset.
   - Acceptance: Two Agents under the same tenant can have different visible knowledge scopes without cross-contaminating retrieval results.

9. **Agent-scoped prompts**
   - Current: Prompts are tenant/task scoped.
   - Target: Agent has an active prompt set mapping task types to prompt versions.
   - Acceptance: Two Agents under the same tenant can run different prompt versions for the same task type.

10. **Agent-scoped runtime data**
   - Target: conversations, runs, feedback, eval runs, alerts, and reports can be filtered by `agent_id`.
   - Acceptance: Agent detail pages only show records for the opened Agent, while tenant-level admin views may aggregate across Agents.

11. **Readiness and activation gate**
   - Target: A draft Agent must pass readiness before activation: model config present, prompt set active, knowledge scope valid if required, channel config checked if enabled, eval suite passed or manually waived.
   - Acceptance: `Activate` is blocked with actionable reasons until readiness passes or an authorized waiver is recorded.

12. **Backward compatibility**
   - Current tenant-scoped APIs and console pages must keep working during migration.
   - Target: Existing tenant console can either remain as an aggregate admin view or redirect to Agent list.
   - Acceptance: Existing pilot data remains readable after introducing Agent IDs, with migration assigning a default Agent per tenant.

## Data Model Proposal

### New tables

#### `agents`

- `id`
- `tenant_id`
- `name`
- `agent_type`
- `description`
- `status`
- `source_agent_id`
- `model_config_ref`
- `prompt_set_id`
- `knowledge_scope_id`
- `risk_policy_id`
- `eval_suite_id`
- `feature_flags_json`
- `created_by`
- `created_at`
- `updated_at`
- `activated_at`
- `archived_at`

#### `agent_prompt_sets`

- `id`
- `agent_id`
- `tenant_id`
- `name`
- `task_prompt_versions_json`
- `created_at`
- `updated_at`

#### `agent_knowledge_scopes`

- `id`
- `agent_id`
- `tenant_id`
- `mode`: `tenant_all`, `document_subset`, `source_file_subset`
- `document_ids_json`
- `source_file_ids_json`
- `created_at`
- `updated_at`

#### `agent_risk_policies`

- `id`
- `agent_id`
- `tenant_id`
- `rules_json`
- `created_at`
- `updated_at`

#### `agent_channel_configs`

- `id`
- `agent_id`
- `tenant_id`
- `channel`: initially `dingtalk`
- `status`: `not_configured`, `configured`, `verified`, `disabled`
- `config_json`: no secrets
- `secret_refs_json`: environment or secret references only
- `created_at`
- `updated_at`

#### `agent_clone_manifests`

- `id`
- `source_agent_id`
- `target_agent_id`
- `tenant_id`
- `options_json`
- `copied_resources_json`
- `referenced_resources_json`
- `reset_resources_json`
- `skipped_resources_json`
- `created_by`
- `created_at`

### Existing table changes

Add nullable `agent_id` to runtime and operational data:

- `conversations`
- `agent_runs`
- `feedback`
- `retrieval_logs` if present
- `model_call_logs`
- `review_items`
- `knowledge_gaps`
- `eval_runs`
- `alerts`
- `pilot_reports`

Migration rule:

- For each existing tenant, create one default Agent named `{tenant.name} Sales Agent`.
- Backfill existing records for that tenant to the default Agent.
- Keep `tenant_id` on all rows. `agent_id` narrows scope; it does not replace tenant isolation.

## Backend API Proposal

New route prefix:

```text
/agents
/agents/{agent_id}
/agents/{agent_id}/clone
/agents/{agent_id}/readiness
/agents/{agent_id}/activate
/agents/{agent_id}/pause
/agents/{agent_id}/archive
/agents/{agent_id}/knowledge-scope
/agents/{agent_id}/prompt-set
/agents/{agent_id}/risk-policy
/agents/{agent_id}/channels
```

Recommended endpoints:

- `GET /agents`: list Agents with filters `tenant_id`, `status`, `agent_type`, `q`.
- `POST /agents`: create draft Agent.
- `GET /agents/{agent_id}`: get Agent detail.
- `PATCH /agents/{agent_id}`: update metadata and feature flags.
- `POST /agents/{agent_id}/clone`: create a draft clone.
- `GET /agents/{agent_id}/clone-manifest`: inspect clone manifest.
- `GET /agents/{agent_id}/readiness`: compute readiness checks.
- `POST /agents/{agent_id}/activate`: activate only when readiness passes or waiver is valid.
- `POST /agents/{agent_id}/pause`: pause runtime use.
- `POST /agents/{agent_id}/archive`: archive inactive Agent.

Agent-scoped wrappers should be added for existing console data:

```text
GET /agents/{agent_id}/conversations
GET /agents/{agent_id}/runs/{run_id}
GET /agents/{agent_id}/knowledge/documents
GET /agents/{agent_id}/prompts
GET /agents/{agent_id}/feedback
GET /agents/{agent_id}/pilot/pilot-metrics
GET /agents/{agent_id}/pilot/review-queue
GET /agents/{agent_id}/pilot/eval-runs
GET /agents/{agent_id}/pilot/alerts
GET /agents/{agent_id}/pilot/reports
```

Implementation may internally call existing tenant-scoped services with `(tenant_id, agent_id)` filters.

## Frontend Proposal

### Routing

Add an Agent-first route tree:

```text
/agents
/agents/new
/agents/:agentId/overview
/agents/:agentId/setup
/agents/:agentId/knowledge
/agents/:agentId/prompts
/agents/:agentId/channels
/agents/:agentId/conversations
/agents/:agentId/traces/:runId
/agents/:agentId/quality
/agents/:agentId/eval
/agents/:agentId/alerts
/agents/:agentId/reports
/agents/:agentId/settings
```

### Navigation

- Replace top `TenantSelector` on Agent pages with an Agent switcher.
- Keep tenant filter only on `/agents` or tenant aggregate admin pages.
- Sidebar should be scoped to the open Agent and show Agent status near the top.

### Pages

1. **Agent List**
   - Cards or table with status, type, tenant, latest activity, readiness, channel status.
   - Primary actions: create, open, clone.

2. **Agent Overview**
   - Runtime status, readiness, usage, feedback, recent conversations, quality alerts.

3. **Agent Setup**
   - Required checklist: model, prompts, knowledge, risk, channel, eval.

4. **Clone Wizard**
   - Source summary.
   - Editable target name and tenant.
   - Copy/reference choices.
   - Secret exclusion warning.
   - Final manifest preview.

5. **Agent Settings**
   - Metadata, status, source Agent, clone manifest, feature flags.

Existing pages can be reused by adding Agent-aware API wrappers and route params.

## Clone Options

Clone wizard should expose these options:

| Resource | Default | Options | Notes |
|---|---|---|---|
| Metadata | copy as draft | copy/edit | Name must be changed before creation if duplicate. |
| Prompt set | copy | copy/reference | Copy is safer for independent future edits. |
| Risk policy | copy | copy/reference | Copy by default to prevent source changes from unexpectedly affecting clone. |
| Knowledge scope | reference | reference/copy subset/empty | Large document duplication should be optional. |
| Eval suite | copy | copy/reference/empty | Copy default to preserve validation baseline. |
| Channel config | shell only | shell only/skip | Secrets and live webhook credentials must not be copied. |
| Runtime history | reset | always reset | Conversations, traces, feedback, alerts, reports are never cloned. |
| Model config | reference | reference/new config | Secrets referenced only, not copied as plaintext. |

## Runtime Behavior

- `/agent/chat` should accept optional `agent_id` once Agent instances exist.
- If only `tenant_id` is provided, route to tenant default active Agent for backward compatibility.
- DingTalk message processing should resolve `agent_id` from channel binding.
- Retrieval must filter by `tenant_id` and Agent knowledge scope.
- Prompt registry must resolve by `agent_id` first, then tenant default as fallback during migration.
- Logging must write both `tenant_id` and `agent_id`.

## Security and Isolation

- `tenant_id` remains mandatory for data isolation.
- `agent_id` must belong to the resolved tenant before any operation.
- Clone must not copy plaintext secrets.
- Channel secret refs must be reconfigured or explicitly reused through a safe secret reference mechanism.
- Archived Agents cannot receive chat traffic.
- Paused Agents can be inspected but not used for runtime chat unless explicitly allowed for admin preview.
- Agent-scoped APIs must prevent reading runtime data from another Agent under the same tenant unless the caller is using a tenant aggregate route.

## Acceptance Criteria

- [ ] A persisted Agent entity exists and can be created, listed, opened, updated, paused, activated, archived, and cloned.
- [ ] Existing tenants are migrated to a default Agent without losing existing data visibility.
- [ ] Frontend has `/agents` and Agent-specific routes using `agentId` instead of global tenant selection.
- [ ] Opening an Agent page shows only that Agent's knowledge, prompts, conversations, feedback, evals, alerts, and reports.
- [ ] Agent clone wizard creates a draft Agent from an existing Agent.
- [ ] Clone manifest records copied, referenced, reset, and skipped resources.
- [ ] Clone does not copy conversations, traces, feedback, alerts, reports, eval run results, or plaintext secrets.
- [ ] Two Agents under one tenant can use different prompt versions.
- [ ] Two Agents under one tenant can use different knowledge scopes.
- [ ] `/agent/chat` can route by `agent_id`, while existing tenant-only calls still work through a default Agent fallback.
- [ ] DingTalk channel binding can resolve the target Agent.
- [ ] Readiness gate blocks activation until required setup checks pass or waiver is recorded.
- [ ] Existing tenant-scoped admin console functionality remains usable during migration.
- [ ] Unit/integration tests cover Agent CRUD, clone behavior, readiness, Agent-scoped filters, and backward compatibility.

## Suggested Implementation Phases for Worker

### Phase 1: Agent data model and migration

- Add Agent models and tables.
- Add `agent_id` to runtime/operations tables.
- Create default Agent per existing tenant.
- Backfill existing data.
- Add tests for migration and ownership checks.

### Phase 2: Agent APIs and clone service

- Implement Agent CRUD/status endpoints.
- Implement clone service and manifest persistence.
- Implement readiness service.
- Add Agent-scoped wrappers for key existing APIs.
- Add tests for clone options and no-secret copying.

### Phase 3: Runtime resolution

- Update `/agent/chat` to accept `agent_id`.
- Resolve default Agent when only tenant is provided.
- Update prompt resolution and retrieval filtering to respect Agent scope.
- Update DingTalk channel resolution to bind messages to Agent.
- Add tests for tenant + Agent isolation.

### Phase 4: Frontend Agent shell

- Add `/agents` list page.
- Add `AgentContext` and Agent switcher.
- Add Agent route tree.
- Rewire existing pages to use `agentId` routes and Agent-scoped APIs.
- Keep tenant selector only for aggregate/admin contexts.

### Phase 5: Clone wizard and activation readiness

- Add create Agent flow.
- Add clone wizard.
- Add setup/readiness page.
- Add activation, pause, archive actions with confirmations.
- Add frontend tests for create/open/clone/activate flows.

## Boundaries

**In scope:**

- Product-level Agent Instance model.
- One-to-one Agent management pages.
- Rapid Agent cloning.
- Agent-scoped prompts, knowledge, channels, runtime data, quality data.
- Backward-compatible migration from tenant-first console.
- Agent activation readiness checks.

**Out of scope:**

- Billing and subscription plans.
- Public marketplace of Agent templates.
- Full RBAC/SSO implementation.
- CRM integration.
- Multi-channel support beyond preparing the Agent/channel abstraction and keeping DingTalk working.
- Automatic prompt optimization.
- Copying customer private secrets or historical conversations into cloned Agents.

## Constraints

- Existing pilot functionality must remain usable during migration.
- Tenant isolation is still the primary security boundary.
- Agent scoping must be enforced in backend queries, not only in frontend routing.
- Clone should prefer independent configuration copies over shared mutable references unless the user explicitly chooses reference mode.
- Runtime history must never be cloned.
- The first version should prioritize reliable copying and clear setup state over visual redesign.
- The UI should stay operational and dense; no marketing landing page is needed.

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|---|---:|---:|---|---|
| Goal Clarity | 0.88 | 0.75 | met | Goal is one Agent equals one product page plus fast clone. |
| Boundary Clarity | 0.84 | 0.70 | met | Billing, marketplace, CRM, RBAC/SSO depth, and auto-optimization are excluded. |
| Constraint Clarity | 0.83 | 0.65 | met | Tenant isolation, no secret copying, no runtime-history cloning, and backward compatibility are explicit. |
| Acceptance Criteria | 0.86 | 0.70 | met | CRUD, routing, clone, readiness, runtime resolution, and tests are covered. |
| **Ambiguity** | **0.16** | **<=0.20** | **met** | Ready for implementation planning. |

## Open Product Decisions

These can be resolved by the worker during planning if needed:

1. Whether cloned knowledge should default to `reference` or `empty` for cross-customer clones. Recommendation: reference only within the same tenant; empty when cloning across tenants.
2. Whether one tenant may own multiple active Agents of the same type. Recommendation: yes, because quick replication and A/B variants need this.
3. Whether prompt sets should be copied as full rows or version references. Recommendation: copy prompt set mappings first; only duplicate prompt text when the user chooses independent copy.
4. Whether `/dashboard` redirects to `/agents` or remains tenant aggregate. Recommendation: redirect normal users to `/agents`; keep tenant aggregate behind an admin route later.

## Worker Handoff Summary

Build `Agent Instance` as a first-class product layer above the existing tenant console. The worker should start with backend data model and migration, then Agent APIs and clone service, then runtime resolution, then frontend route/context conversion. Keep all existing tenant APIs working until Agent-scoped pages are complete.

---

*Phase: agent-instance-one-to-one-clone*
*Spec created: 2026-06-15*
*Next step: create implementation plan and split into backend model/migration, clone service, runtime binding, frontend Agent shell, and clone wizard.*
