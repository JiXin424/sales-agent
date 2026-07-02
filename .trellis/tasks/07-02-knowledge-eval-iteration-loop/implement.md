# Knowledge Evaluation Optimization Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-tenant, trace-attributed knowledge optimization loop with isolated candidates, human-approved publication, diverse next-round questions, and reproducible LangGraph rollback.

**Architecture:** Add immutable release/version records around the existing Agent runtime, then enrich eval persistence with route and ranked-retrieval traces. A deterministic diagnoser gates a restricted optimization LangGraph; a PostgreSQL-leased worker evaluates candidates and exposes progress through Agent-scoped APIs, the existing React console, and CLI commands.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy asyncio, Alembic, PostgreSQL/pgvector, LangGraph/PostgresSaver, DeepEval, Typer, React 18, TypeScript, Ant Design, TanStack Query, SSE, pytest, Vitest.

---

## Delivery Order and Checkpoints

This is one product feature with five sequential, independently releasable tracks:

1. Versioned runtime foundation.
2. Evaluation trace and deterministic attribution.
3. Restricted optimizer, worker, gates, release, and rollback.
4. Fact inventory and question evolution.
5. Web console and CLI operations.

Do not begin a later track until the prior track's full regression command passes. Create a commit after every task. Keep the production `agent_runtime_bindings` behavior disabled behind `feature_flags_json["knowledge_iteration"]` until Task 15 completes.

## Track 1 — Versioned Runtime Foundation

### Task 1: Add immutable knowledge and runtime version models

**Files:**
- Create: `src/sales_agent/models/knowledge_version.py`
- Create: `src/sales_agent/models/runtime_release.py`
- Modify: `src/sales_agent/models/document.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/test_runtime_version_models.py`

- [ ] **Step 1: Write the failing metadata test**

```python
from sales_agent.core.database import Base

def test_version_tables_are_registered():
    expected = {
        "document_revisions", "knowledge_versions", "knowledge_version_documents",
        "retrieval_profiles", "router_profiles", "optimization_releases",
        "agent_runtime_bindings", "release_events",
    }
    assert expected <= set(Base.metadata.tables)

def test_document_chunks_are_scoped_to_a_version():
    columns = Base.metadata.tables["document_chunks"].c
    assert columns.knowledge_version_id.nullable is True
    assert columns.document_revision_id.nullable is True
```

- [ ] **Step 2: Run the test and confirm missing-table failure**

Run: `pytest tests/unit/test_runtime_version_models.py -q`

Expected: FAIL because the new tables and chunk columns do not exist.

- [ ] **Step 3: Implement focused SQLAlchemy models**

Define `DocumentRevision`, `KnowledgeVersion`, `KnowledgeVersionDocument`, `RetrievalProfile`, and `RouterProfile` in `knowledge_version.py`. Define `OptimizationRelease`, `AgentRuntimeBinding`, and `ReleaseEvent` in `runtime_release.py`. Use `Text` IDs and the existing `TimestampMixin`; place `tenant_id` on every table and add composite indexes beginning with `tenant_id`.

The runtime pointer must include optimistic locking:

```python
class AgentRuntimeBinding(TimestampMixin, Base):
    __tablename__ = "agent_runtime_bindings"
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    active_release_id: Mapped[str] = mapped_column(Text, nullable=False)
    previous_release_id: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(nullable=False, default=1)
    activated_at: Mapped[str | None] = mapped_column(Text)
    activated_by: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", name="uq_runtime_binding_tenant_agent"),
    )
```

Add nullable `knowledge_version_id`, `document_revision_id`, `chunker_version`, and `chunk_config_hash` to `DocumentChunk` for backward-compatible migration.

- [ ] **Step 4: Run model tests**

Run: `pytest tests/unit/test_runtime_version_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/models tests/unit/test_runtime_version_models.py
git commit -m "feat: add versioned knowledge runtime models"
```

### Task 2: Migrate existing tenants into baseline releases

**Files:**
- Create: `src/sales_agent/migrations/versions/0004_knowledge_iteration_foundation.py`
- Create: `src/sales_agent/services/runtime_version_bootstrap.py`
- Test: `tests/unit/test_runtime_version_bootstrap.py`
- Test: `tests/unit/test_entrypoint_migration.py`

- [ ] **Step 1: Write bootstrap idempotency tests**

```python
@pytest.mark.asyncio
async def test_bootstrap_creates_one_binding_per_agent(db_session, active_agent):
    svc = RuntimeVersionBootstrap(db_session)
    first = await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)
    second = await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)
    assert first.release_id == second.release_id
    count = await db_session.scalar(select(func.count()).select_from(AgentRuntimeBinding))
    assert count == 1
```

- [ ] **Step 2: Run the bootstrap test and confirm import failure**

Run: `pytest tests/unit/test_runtime_version_bootstrap.py -q`

Expected: FAIL because `RuntimeVersionBootstrap` does not exist.

- [ ] **Step 3: Add the Alembic migration and bootstrap service**

Migration `0004` creates the eight foundation tables, adds nullable chunk version columns, and creates indexes for `(tenant_id, knowledge_version_id)` and `(tenant_id, document_revision_id)`. The bootstrap service must create revision 1 for every active document, one baseline knowledge version, retrieval profile, router profile, release manifest, and runtime binding in one transaction. Compute `manifest_hash` from canonical sorted JSON.

- [ ] **Step 4: Verify upgrade and idempotency**

Run:

```bash
pytest tests/unit/test_runtime_version_bootstrap.py tests/unit/test_entrypoint_migration.py -q
alembic upgrade head
alembic current
```

Expected: tests PASS and Alembic reports `0004_knowledge_iteration_foundation`.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/migrations/versions/0004_knowledge_iteration_foundation.py src/sales_agent/services/runtime_version_bootstrap.py tests/unit
git commit -m "feat: bootstrap baseline knowledge releases"
```

### Task 3: Resolve and atomically switch release manifests

**Files:**
- Create: `src/sales_agent/services/release_service.py`
- Create: `src/sales_agent/services/release_types.py`
- Test: `tests/unit/test_release_service.py`

- [ ] **Step 1: Write tenant and optimistic-lock tests**

```python
@pytest.mark.asyncio
async def test_activate_rejects_stale_lock(db_session, binding, candidate_release):
    service = ReleaseService(db_session)
    with pytest.raises(StaleRuntimeBinding):
        await service.activate(
            tenant_id=binding.tenant_id,
            agent_id=binding.agent_id,
            release_id=candidate_release.id,
            expected_lock_version=binding.lock_version - 1,
            actor_id="reviewer",
        )

@pytest.mark.asyncio
async def test_resolve_never_crosses_tenant(db_session, release_other_tenant):
    with pytest.raises(ReleaseNotFound):
        await ReleaseService(db_session).get_manifest("tenant-a", release_other_tenant.id)
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/test_release_service.py -q`

Expected: FAIL on missing service and exception types.

- [ ] **Step 3: Implement immutable manifest resolution and switching**

Expose `ReleaseManifest` as a frozen dataclass with release, knowledge, retrieval, router, prompt set, model snapshot, graph definition, and code revision IDs. `activate()` must update the binding using `WHERE tenant_id=:tenant AND agent_id=:agent AND lock_version=:expected`, increment the lock, preserve the previous release, and append a `ReleaseEvent` in the same transaction.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_release_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/services/release_service.py src/sales_agent/services/release_types.py tests/unit/test_release_service.py
git commit -m "feat: resolve and switch runtime releases"
```

### Task 4: Pin release versions in ChatPipeline and LangGraph state

**Files:**
- Modify: `src/sales_agent/graph/state.py`
- Create: `src/sales_agent/graph/nodes/release_resolution.py`
- Modify: `src/sales_agent/graph/chat_graph.py`
- Modify: `src/sales_agent/services/chat_pipeline.py`
- Modify: `src/sales_agent/services/retriever.py`
- Test: `tests/unit/graph/test_release_resolution_node.py`
- Test: `tests/integration/test_graph_pipeline_parity.py`

- [ ] **Step 1: Write pinned-version tests**

```python
@pytest.mark.asyncio
async def test_release_resolution_pins_manifest(fake_runtime, active_manifest):
    result = await resolve_release_node(
        {"tenant_id": "t1", "agent_id": "a1"}, fake_runtime
    )
    assert result["release_id"] == active_manifest.release_id
    assert result["knowledge_version_id"] == active_manifest.knowledge_version_id

@pytest.mark.asyncio
async def test_retrieval_filters_pinned_knowledge_version(retriever, vector_store):
    await retriever.retrieve("t1", "query", knowledge_version_id="kv2")
    assert vector_store.last_filters == {"tenant_id": "t1", "knowledge_version_id": "kv2"}
```

- [ ] **Step 2: Confirm tests fail**

Run: `pytest tests/unit/graph/test_release_resolution_node.py tests/integration/test_graph_pipeline_parity.py -q`

Expected: FAIL because state and retrieval signatures lack version IDs.

- [ ] **Step 3: Implement pinning with backward compatibility**

Add release/version fields to `ChatGraphState`. Insert release resolution after tenant resolution. Resolve once in `ChatPipeline.execute()` and pass the pinned knowledge version through `retrieve_for_task()`, `HybridRetriever.retrieve()`, and vector/keyword database filters. When the feature flag is disabled or no binding exists, use the baseline compatibility path and record `release_id=None`.

- [ ] **Step 4: Run focused and parity tests**

Run: `pytest tests/unit/graph tests/unit/test_path_router.py tests/integration/test_graph_pipeline_parity.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph src/sales_agent/services/chat_pipeline.py src/sales_agent/services/retriever.py tests
git commit -m "feat: pin release manifests in agent runs"
```

## Track 2 — Evaluation Trace and Attribution

### Task 5: Extend eval persistence and ranked retrieval trace tables

**Files:**
- Modify: `src/sales_agent/models/eval.py`
- Create: `src/sales_agent/models/eval_trace.py`
- Create: `src/sales_agent/migrations/versions/0005_eval_trace_schema.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/test_eval_trace_models.py`

- [ ] **Step 1: Write schema registration and uniqueness tests**

```python
def test_eval_trace_tables_are_registered():
    assert {"eval_metric_results", "retrieval_traces", "retrieval_trace_hits"} <= set(Base.metadata.tables)

def test_metric_applicability_is_persisted():
    assert "applicability" in Base.metadata.tables["eval_metric_results"].c
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/unit/test_eval_trace_models.py -q`

Expected: FAIL on missing tables.

- [ ] **Step 3: Add models and migration**

Extend suites, cases, runs, and results with the exact fields from `design.md` section 7.1. Add normalized metric, retrieval-trace, and hit tables. Keep new columns nullable or server-defaulted so existing rows remain readable. Add `(tenant_id, retrieval_trace_id, channel, channel_rank)` and `(tenant_id, document_revision_id)` indexes.

- [ ] **Step 4: Verify model and migration tests**

Run: `pytest tests/unit/test_eval_trace_models.py tests/unit/test_entrypoint_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/models src/sales_agent/migrations/versions/0005_eval_trace_schema.py tests/unit
git commit -m "feat: persist evaluation and retrieval traces"
```

### Task 6: Capture route and per-channel retrieval evidence

**Files:**
- Modify: `src/sales_agent/services/run_tracer.py`
- Modify: `src/sales_agent/services/task_router.py`
- Modify: `src/sales_agent/services/retriever.py`
- Modify: `src/sales_agent/rag/keyword_retriever.py`
- Modify: `eval/deepeval_test_cases.py`
- Test: `tests/unit/test_run_tracer.py`
- Test: `tests/unit/test_retrieval_trace.py`

- [ ] **Step 1: Write trace contract tests**

```python
def test_agent_response_contains_route_and_retrieval_trace():
    response = AgentResponse()
    assert response.route_trace == {}
    assert response.retrieval_trace == {}

@pytest.mark.asyncio
async def test_hybrid_trace_preserves_both_channel_ranks(hybrid_retriever):
    result = await hybrid_retriever.retrieve("t1", "视听会员", knowledge_version_id="kv1")
    channels = {(h.chunk_id, h.channel) for h in result.trace_hits}
    assert any(channel == "vector" for _, channel in channels)
    assert any(channel == "keyword" for _, channel in channels)
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/unit/test_run_tracer.py tests/unit/test_retrieval_trace.py -q`

Expected: FAIL because trace fields and hit records are absent.

- [ ] **Step 3: Add safe structured traces**

Return router type, confidence, LLM-called flag, needs-retrieval, and decision reason. Preserve vector rank/score and keyword rank/score before RRF fusion, final rank/score, selected status, document revision, and chunk ID. Persist text only in the existing chunk table; trace rows reference chunk IDs to avoid duplication. Extend `_sanitize_metadata()` to redact nested lists and dictionaries.

- [ ] **Step 4: Run trace and retrieval tests**

Run: `pytest tests/unit/test_run_tracer.py tests/unit/test_retrieval_trace.py tests/unit/graph/test_retrieval_node.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/services src/sales_agent/rag/keyword_retriever.py eval/deepeval_test_cases.py tests/unit
git commit -m "feat: capture route and ranked retrieval evidence"
```

### Task 7: Make DeepEval metrics applicable and persist full eval runs

**Files:**
- Modify: `eval/deepeval_metrics.py`
- Modify: `eval/deepeval_eval.py`
- Create: `eval/deepeval_persistence.py`
- Modify: `src/sales_agent/services/eval_runner_service.py`
- Test: `tests/test_deepeval_agent.py`
- Test: `tests/unit/test_eval_persistence.py`

- [ ] **Step 1: Write empty-context and persistence tests**

```python
def test_answer_recall_is_not_applicable_without_context(judge):
    result = evaluate_metric(AnswerRecallMetric(model=judge), test_case(retrieval_context=[]))
    assert result.applicability == "not_applicable"
    assert result.score is None

@pytest.mark.asyncio
async def test_eval_persistence_stores_metric_reason(db_session, evaluated_case):
    await EvalPersistence(db_session).save_case(evaluated_case)
    row = await db_session.scalar(select(EvalMetricResult))
    assert row.reason == evaluated_case.metrics[0].reason
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/test_deepeval_agent.py tests/unit/test_eval_persistence.py -q`

Expected: FAIL because empty context currently yields a perfect AnswerRecall and script output is file-only.

- [ ] **Step 3: Implement applicability and one real eval path**

Return a normalized metric result with nullable score and applicability. Skip Faithfulness and AnswerRecall when retrieval context is empty. Build TaskCompletion's task text from `question_type` and expected route. Replace `EvalRunnerService._execute_case()` placeholder behavior with the same ChatPipeline/DeepEval adapter used by `deepeval_eval.py`; keep JSON/HTML exports as projections of persisted runs.

- [ ] **Step 4: Run eval tests**

Run: `pytest tests/test_deepeval_agent.py tests/unit/test_eval_persistence.py tests/integration/test_pilot_api.py -q`

Expected: PASS and no placeholder `passed=true` path remains.

- [ ] **Step 5: Commit**

```bash
git add eval src/sales_agent/services/eval_runner_service.py tests
git commit -m "feat: persist applicable DeepEval results"
```

### Task 8: Implement deterministic attribution and oracle probes

**Files:**
- Create: `src/sales_agent/optimization/types.py`
- Create: `src/sales_agent/optimization/oracle.py`
- Create: `src/sales_agent/optimization/diagnoser.py`
- Create: `src/sales_agent/optimization/clustering.py`
- Test: `tests/unit/optimization/test_diagnoser.py`
- Test: `tests/unit/optimization/test_oracle.py`

- [ ] **Step 1: Write one fixture per diagnosis branch**

```python
@pytest.mark.parametrize(("fixture_name", "cause"), [
    ("route_skipped", "route_miss"),
    ("fact_absent", "document_missing"),
    ("gold_outside_top30", "retrieval_recall"),
    ("gold_rank_12", "retrieval_ranking"),
    ("gold_selected_noisy", "context_noise"),
    ("gold_selected_answer_wrong", "generation_issue"),
])
def test_primary_cause_order(load_trace_fixture, fixture_name, cause):
    diagnosis = FailureDiagnoser().diagnose(load_trace_fixture(fixture_name))
    assert diagnosis.primary_cause == cause
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization -q`

Expected: FAIL because the optimization package does not exist.

- [ ] **Step 3: Implement ordered rules and oracle interface**

Define `FailureDiagnosis(primary_cause, secondary_causes, confidence, evidence, blocked_checks, recommended_action)`. The oracle must query only `(tenant_id, knowledge_version_id)`, use required facts and source lineage, and return `present`, `absent`, `conflicting`, or `invalid_lineage`. Do not invoke the production retriever inside the oracle.

- [ ] **Step 4: Run optimization unit tests**

Run: `pytest tests/unit/optimization -q`

Expected: PASS for every ordered branch and cross-tenant rejection.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/optimization tests/unit/optimization
git commit -m "feat: attribute evaluation failures deterministically"
```

## Track 3 — Optimizer, Worker, Release, and Rollback

### Task 9: Add iteration, diagnosis, candidate, checkpoint, and job models

**Files:**
- Create: `src/sales_agent/models/optimization.py`
- Create: `src/sales_agent/migrations/versions/0006_optimization_workflow.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/optimization/test_models.py`

- [ ] **Step 1: Write model and single-change-category tests**

```python
def test_optimization_tables_are_registered():
    expected = {
        "optimization_iterations", "failure_diagnoses", "optimization_candidates",
        "candidate_eval_runs", "iteration_graph_checkpoints", "optimization_jobs",
    }
    assert expected <= set(Base.metadata.tables)

def test_candidate_change_type_is_single_enum_value():
    assert OptimizationCandidate.ALLOWED_CHANGE_TYPES == {"router", "retrieval", "document"}
```

- [ ] **Step 2: Confirm missing-model failure**

Run: `pytest tests/unit/optimization/test_models.py -q`

Expected: FAIL.

- [ ] **Step 3: Add models and migration**

Implement exact iteration fields from the approved design plus `OptimizationJob(idempotency_key, stage, status, lease_owner, lease_expires_at, attempts, payload_json, error_json)`. Add unique `(tenant_id, agent_id, iteration_no)` and `(tenant_id, idempotency_key)` constraints.

- [ ] **Step 4: Run tests and migration**

Run: `pytest tests/unit/optimization/test_models.py tests/unit/test_entrypoint_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/models src/sales_agent/migrations/versions/0006_optimization_workflow.py tests/unit/optimization
git commit -m "feat: persist optimization workflow state"
```

### Task 10: Implement constrained candidate tools

**Files:**
- Create: `src/sales_agent/optimization/tools.py`
- Create: `src/sales_agent/optimization/patch_validation.py`
- Create: `src/sales_agent/optimization/candidate_service.py`
- Test: `tests/unit/optimization/test_tools.py`

- [ ] **Step 1: Write permission and evidence tests**

```python
@pytest.mark.asyncio
async def test_tool_ignores_model_supplied_tenant(tool_context):
    result = await propose_retrieval_patch.ainvoke(
        {"tenant_id": "other", "synonyms": {"视频": ["视听"]}},
        config=tool_context("tenant-a"),
    )
    assert result.tenant_id == "tenant-a"

def test_document_patch_without_evidence_becomes_gap():
    result = validate_document_patch(DocumentPatch(evidence_ids=[], diff="+ invented"))
    assert result.action == "create_knowledge_gap"
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/test_tools.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement tool schemas and allowlists**

Tool tenant and Agent IDs come from runtime context only. Validate router, retrieval, and document patches against separate Pydantic schemas. Reject mixed change types and non-allowlisted fields. Store a canonical patch hash to deduplicate candidates.

- [ ] **Step 4: Run tool tests**

Run: `pytest tests/unit/optimization/test_tools.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/optimization tests/unit/optimization/test_tools.py
git commit -m "feat: constrain knowledge optimization tools"
```

### Task 11: Build the optimization LangGraph and PostgreSQL worker

**Files:**
- Create: `src/sales_agent/optimization/state.py`
- Create: `src/sales_agent/optimization/graph.py`
- Create: `src/sales_agent/optimization/nodes.py`
- Create: `src/sales_agent/optimization/worker.py`
- Test: `tests/unit/optimization/test_graph.py`
- Test: `tests/unit/optimization/test_worker.py`

- [ ] **Step 1: Write transition and lease tests**

```python
def test_graph_routes_human_only_diagnosis_to_review():
    graph = build_optimization_graph().compile()
    result = graph.invoke({"diagnosis": {"recommended_action": "human_review"}})
    assert result["status"] == "needs_human"

@pytest.mark.asyncio
async def test_worker_lease_is_exclusive(db_session, queued_job):
    first, second = await asyncio.gather(worker_a.lease_one(), worker_b.lease_one())
    assert sum(job is not None for job in (first, second)) == 1
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/test_graph.py tests/unit/optimization/test_worker.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement resumable graph and leasing**

Graph stages are `baseline`, `diagnose`, `propose`, `build`, `targeted_eval`, `regression_eval`, `awaiting_approval`, `publish`, and `question_evolution`. Compile with the shared Postgres checkpointer. Worker leasing uses `SELECT ... FOR UPDATE SKIP LOCKED`, lease expiry, heartbeat, bounded retry, and `(tenant, iteration, candidate, stage)` idempotency.

- [ ] **Step 4: Run graph/worker tests**

Run: `pytest tests/unit/optimization/test_graph.py tests/unit/optimization/test_worker.py -q`

Expected: PASS, including recovery of an expired lease without duplicating stage output.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/optimization tests/unit/optimization
git commit -m "feat: run optimization as resumable LangGraph jobs"
```

### Task 12: Build sandbox versions and staged release gates

**Files:**
- Create: `src/sales_agent/optimization/sandbox.py`
- Create: `src/sales_agent/optimization/gates.py`
- Create: `src/sales_agent/optimization/evaluator.py`
- Modify: `src/sales_agent/services/knowledge_ingestor.py`
- Test: `tests/integration/test_optimization_sandbox.py`
- Test: `tests/unit/optimization/test_gates.py`

- [ ] **Step 1: Write isolation and hard-gate tests**

```python
@pytest.mark.asyncio
async def test_candidate_chunks_do_not_enter_active_release(sandbox, active_release):
    candidate = await sandbox.build(document_patch())
    assert candidate.knowledge_version_id != active_release.knowledge_version_id
    assert await search_active("new anchor") == []

def test_safety_failure_cannot_be_offset_by_score_gain():
    decision = ReleaseGates().evaluate(metrics(overall_gain=.40, leakage_failures=1))
    assert decision.allowed is False
    assert "cross_tenant_leakage" in decision.hard_failures
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/integration/test_optimization_sandbox.py tests/unit/optimization/test_gates.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement isolated builds and staged evaluation**

Create immutable candidate revisions and knowledge/profile versions. Ingest chunks under candidate `knowledge_version_id`. Evaluate targeted, sibling, fixed, safety, and public cross-tenant suites with early stopping. The gate result stores target improvement, fixed regression, fact errors, fabrication, safety, latency, tokens, and error rate separately.

- [ ] **Step 4: Run sandbox and gate tests**

Run: `pytest tests/integration/test_optimization_sandbox.py tests/unit/optimization/test_gates.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/optimization src/sales_agent/services/knowledge_ingestor.py tests
git commit -m "feat: evaluate isolated optimization candidates"
```

### Task 13: Add approval, publication, canary, rollback, and checkpoint fork APIs

**Files:**
- Create: `src/sales_agent/api/routes/optimization.py`
- Create: `src/sales_agent/api/optimization_schemas.py`
- Modify: `src/sales_agent/main.py`
- Modify: `src/sales_agent/api/routes/graph_debug.py`
- Modify: `src/sales_agent/services/release_service.py`
- Test: `tests/integration/test_optimization_api.py`
- Test: `tests/integration/test_optimization_time_travel.py`

- [ ] **Step 1: Write approval and replay authorization tests**

```python
@pytest.mark.asyncio
async def test_publish_requires_accepted_gate_and_human_actor(client, candidate):
    response = await client.post(f"/agents/{candidate.agent_id}/optimization/candidates/{candidate.id}/publish")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_checkpoint_fork_rejects_other_tenant(client, foreign_checkpoint):
    response = await client.post(f"/agents/a1/optimization/checkpoints/{foreign_checkpoint.id}/fork", json={"candidate_id": "c1"})
    assert response.status_code == 404
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/integration/test_optimization_api.py tests/integration/test_optimization_time_travel.py -q`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement Agent-scoped lifecycle endpoints**

Expose create/list/detail/cancel iteration, list diagnosis/candidates/evals, approve/reject, publish, rollback, release compare, checkpoint replay, and checkpoint fork. Validate Agent tenant ownership on every route. Publication creates a new immutable release, switches the pointer transactionally, runs canary, and automatically creates another rollback release on hard failure. Optimization thread IDs use `kbopt:{tenant}:{iteration}:{candidate}:{run}` and require database ownership, not prefix-only authorization.

- [ ] **Step 4: Run lifecycle tests**

Run: `pytest tests/integration/test_optimization_api.py tests/integration/test_optimization_time_travel.py tests/integration/test_graph_pipeline_parity.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/api src/sales_agent/main.py src/sales_agent/services/release_service.py tests/integration
git commit -m "feat: publish and roll back optimization releases"
```

## Track 4 — Fact Inventory and Question Evolution

### Task 14: Persist versioned facts and corpus conflicts

**Files:**
- Create: `src/sales_agent/models/knowledge_fact.py`
- Create: `src/sales_agent/migrations/versions/0007_knowledge_facts.py`
- Create: `src/sales_agent/optimization/fact_inventory.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/optimization/test_fact_inventory.py`

- [ ] **Step 1: Write deduplication and conflict tests**

```python
@pytest.mark.asyncio
async def test_fact_hash_is_stable_across_extraction_order(inventory):
    a = await inventory.store(fact(object_values=["A", "B"]))
    b = await inventory.store(fact(object_values=["B", "A"]))
    assert a.id == b.id

@pytest.mark.asyncio
async def test_conflicting_effective_facts_are_flagged(inventory):
    rows = await inventory.store_many([discount("5折"), discount("6折")])
    assert {row.conflict_status for row in rows} == {"conflicting"}
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/test_fact_inventory.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement fact extraction persistence**

Store subject, predicate, normalized object JSON, qualifiers, evidence offsets, effective dates, document revision, extractor/version, conflict status, and canonical fact hash. Conflicts produce review items and cannot seed an ordinary factual question.

- [ ] **Step 4: Run fact tests**

Run: `pytest tests/unit/optimization/test_fact_inventory.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/models src/sales_agent/migrations/versions/0007_knowledge_facts.py src/sales_agent/optimization/fact_inventory.py tests/unit/optimization
git commit -m "feat: build versioned knowledge fact inventory"
```

### Task 15: Replace chunk-only synthesis with diverse exploration suites

**Files:**
- Create: `eval/question_evolution.py`
- Create: `eval/question_quality.py`
- Modify: `eval/deepeval_synthesize.py`
- Modify: `eval/deepeval_dataset.py`
- Test: `tests/unit/test_question_evolution.py`
- Test: `tests/unit/test_question_quality.py`

- [ ] **Step 1: Write distribution, lineage, and anti-leakage tests**

```python
def test_generator_respects_distribution(generator, fact_inventory):
    suite = generator.generate(fact_inventory, seed=17, size=100)
    assert count_type(suite, "factual") == 25
    assert count_type(suite, "unanswerable") == 10

def test_question_records_fact_lineage(question):
    assert question.source_fact_ids
    assert question.required_facts
    assert question.generator_version

def test_new_suite_cannot_gate_its_parent_release(release, generated_suite):
    assert generated_suite.knowledge_version_id == release.knowledge_version_id
    assert generated_suite.id not in release.approval_eval_suite_ids
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/test_question_evolution.py tests/unit/test_question_quality.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement role-based two-stage generation**

Generate questions from structured facts, not full source paragraphs. Use seeded stratified sampling and the approved 25/20/15/20/10/10 distribution. Add role/language perturbation, entity-linked cross-document selection, oracle-verified unanswerables, semantic deduplication, per-document/fact caps, and quality quarantine. Keep the current file exporters for compatibility.

- [ ] **Step 4: Run synthesis tests and a deterministic smoke generation**

Run:

```bash
pytest tests/unit/test_question_evolution.py tests/unit/test_question_quality.py -q
python eval/deepeval_synthesize.py --docs-dir data/sales-agent/tenants/taishan/documents --output /tmp/question-smoke --max-goldens 20
```

Expected: tests PASS; smoke output contains multiple question types with source lineage and no duplicate normalized inputs.

- [ ] **Step 5: Commit**

```bash
git add eval tests/unit/test_question_evolution.py tests/unit/test_question_quality.py
git commit -m "feat: evolve diverse exploration question suites"
```

### Task 16: Add promotion workflow and post-release trigger

**Files:**
- Create: `src/sales_agent/services/question_suite_service.py`
- Modify: `src/sales_agent/optimization/nodes.py`
- Modify: `src/sales_agent/api/routes/optimization.py`
- Test: `tests/integration/test_question_suite_promotion.py`

- [ ] **Step 1: Write immutability and promotion tests**

```python
@pytest.mark.asyncio
async def test_promotion_creates_new_fixed_suite(db_session, fixed_v3, exploration_case):
    fixed_v4 = await QuestionSuiteService(db_session).promote(
        tenant_id=fixed_v3.tenant_id,
        source_case_ids=[exploration_case.id],
        target_fixed_suite_id=fixed_v3.id,
        actor_id="reviewer",
    )
    assert fixed_v4.parent_suite_id == fixed_v3.id
    assert fixed_v3.case_count < fixed_v4.case_count
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/integration/test_question_suite_promotion.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement promotion and release completion trigger**

After successful canary, enqueue exploration generation for the published knowledge version. Promotion copies accepted cases into a new immutable fixed suite; it never mutates the previous suite. Record actor, lineage, content hash, and generator version.

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/integration/test_question_suite_promotion.py tests/integration/test_optimization_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/services/question_suite_service.py src/sales_agent/optimization/nodes.py src/sales_agent/api/routes/optimization.py tests/integration
git commit -m "feat: promote and regenerate evaluation suites"
```

## Track 5 — Web Console and CLI

### Task 17: Add typed optimization API client and route shell

**Files:**
- Create: `console/src/api/optimization.ts`
- Modify: `console/src/api/types.ts`
- Modify: `console/src/api/index.ts`
- Modify: `console/src/App.tsx`
- Modify: `console/src/layout/AgentLayout.tsx`
- Create: `console/src/pages/Agents/KnowledgeIterationPage.tsx`
- Test: `console/src/tests/api/optimization.test.ts`

- [ ] **Step 1: Write API path tests**

```typescript
it('starts an agent-scoped iteration', async () => {
  server.use(http.post('/api/agents/a1/optimization/iterations', () => HttpResponse.json({ id: 'i1' })));
  const result = await startIteration('a1', request);
  expect(result.id).toBe('i1');
});
```

- [ ] **Step 2: Confirm failure**

Run: `cd console && npm test -- optimization.test.ts`

Expected: FAIL because the API module and route do not exist.

- [ ] **Step 3: Implement typed client, navigation, and page shell**

Add `/agents/:agentId/optimization`, an Agent sidebar item labeled `知识迭代`, query keys, and request/response types for iterations, diagnoses, candidates, eval comparison, releases, checkpoints, and question suites. The initial page shows start controls, current iteration status, and empty/loading/error states.

- [ ] **Step 4: Run client tests and TypeScript build**

Run: `cd console && npm test -- optimization.test.ts && npm run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/src
git commit -m "feat(console): add knowledge iteration workspace"
```

### Task 18: Build live overview, attribution, candidates, and eval comparison

**Files:**
- Create: `console/src/pages/Agents/knowledge-iteration/IterationOverview.tsx`
- Create: `console/src/pages/Agents/knowledge-iteration/AttributionPanel.tsx`
- Create: `console/src/pages/Agents/knowledge-iteration/CandidateDiffPanel.tsx`
- Create: `console/src/pages/Agents/knowledge-iteration/EvalComparisonPanel.tsx`
- Create: `console/src/hooks/useOptimizationStream.ts`
- Modify: `console/src/pages/Agents/KnowledgeIterationPage.tsx`
- Test: `console/src/pages/Agents/knowledge-iteration/KnowledgeIterationPage.test.tsx`

- [ ] **Step 1: Write regression-visibility and SSE recovery tests**

```typescript
it('shows regressions separately from aggregate gains', async () => {
  render(<EvalComparisonPanel comparison={comparisonWithOneRegression} />);
  expect(screen.getByText('新增退化 1')).toBeInTheDocument();
});

it('reconnects stream with the last event id', async () => {
  const stream = renderHook(() => useOptimizationStream('a1', 'i1'));
  expect(stream.result.current.reconnectFrom).toBe(lastEventId);
});
```

- [ ] **Step 2: Confirm failures**

Run: `cd console && npm test -- KnowledgeIterationPage.test.tsx`

Expected: FAIL.

- [ ] **Step 3: Implement the four operational panels**

Use Ant Design tabs, tables, progress, descriptions, and diff code blocks. Show stage/budget, ordered attribution evidence, hypothesis/change scope, before/after by metric/type/case, new passes, regressions, Judge instability, and operational cost. Persist last SSE event ID and refetch durable state after reconnect.

- [ ] **Step 4: Run UI tests and build**

Run: `cd console && npm test -- KnowledgeIterationPage.test.tsx && npm run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/src
git commit -m "feat(console): inspect optimization diagnoses and candidates"
```

### Task 19: Add releases, time travel, rollback, and question review UI

**Files:**
- Create: `console/src/pages/Agents/knowledge-iteration/ReleaseGraphPanel.tsx`
- Create: `console/src/pages/Agents/knowledge-iteration/CheckpointReplayPanel.tsx`
- Create: `console/src/pages/Agents/knowledge-iteration/QuestionReviewPanel.tsx`
- Modify: `console/src/pages/Agents/CheckpointDAG.tsx`
- Modify: `console/src/pages/Agents/KnowledgeIterationPage.tsx`
- Test: `console/src/pages/Agents/knowledge-iteration/ReleaseOperations.test.tsx`

- [ ] **Step 1: Write rollback confirmation and tenant-safe replay tests**

```typescript
it('renders every version change before rollback confirmation', async () => {
  render(<ReleaseGraphPanel releases={releases} />);
  await user.click(screen.getByRole('button', { name: '回滚至 release_12' }));
  expect(screen.getByText('kb_v18 → kb_v15')).toBeInTheDocument();
  expect(screen.getByText('r9 → r7')).toBeInTheDocument();
});
```

- [ ] **Step 2: Confirm failure**

Run: `cd console && npm test -- ReleaseOperations.test.tsx`

Expected: FAIL.

- [ ] **Step 3: Implement release DAG and review operations**

Render immutable release/candidate lineage, manifest comparison, replay/fork controls, two-step rollback confirmation, exploration-question metadata, quarantine reasons, and fixed-suite promotion selection. Reuse `CheckpointDAG` layout while keeping optimization authorization/API calls separate from debug threads.

- [ ] **Step 4: Run console suite**

Run: `cd console && npm test && npm run build`

Expected: all Vitest tests and TypeScript build PASS.

- [ ] **Step 5: Commit**

```bash
git add console/src
git commit -m "feat(console): manage releases and evolving question suites"
```

### Task 20: Add CLI operations backed by the same APIs

**Files:**
- Create: `src/sales_agent/cli_optimization.py`
- Modify: `src/sales_agent/cli.py`
- Test: `tests/unit/test_optimization_cli.py`

- [ ] **Step 1: Write Typer command tests**

```python
def test_iteration_start_calls_agent_scoped_api(runner, api_mock):
    result = runner.invoke(app, ["iteration", "start", "--agent", "a1", "--fixed-suite", "fixed_v3"])
    assert result.exit_code == 0
    assert api_mock.last_path == "/agents/a1/optimization/iterations"

def test_release_rollback_requires_confirmation(runner):
    result = runner.invoke(app, ["release", "rollback", "release_12", "--agent", "a1"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/test_optimization_cli.py -q`

Expected: FAIL.

- [ ] **Step 3: Add `iteration`, `release`, and `checkpoint` Typer groups**

Implement start/watch/compare/approve, release rollback, checkpoint replay/fork, and report export. Resolve API base URL and credentials from existing environment configuration. Print structured errors and require `--yes` for destructive pointer changes. Do not duplicate business logic in the CLI.

- [ ] **Step 4: Run CLI and full regression**

Run:

```bash
pytest tests/unit/test_optimization_cli.py -q
pytest tests/unit tests/integration -q
cd console && npm test && npm run build
```

Expected: all commands PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/cli.py src/sales_agent/cli_optimization.py tests/unit/test_optimization_cli.py
git commit -m "feat: operate knowledge iterations from CLI"
```

## Final Verification and Rollout

### Task 21: Run the complete multi-tenant vertical slice

**Files:**
- Create: `tests/integration/test_knowledge_iteration_end_to_end.py`
- Modify: `README.md`
- Modify: `eval/README_DEEPEVAL.md`
- Create: `docs/knowledge-iteration-ops.md`

- [ ] **Step 1: Add one end-to-end fixture with three independent failures**

The fixture contains:

```text
tenant-a: one route miss, one synonym recall miss, one missing fact
tenant-b: similarly named documents that must never appear in tenant-a traces
```

Assert that the system proposes router, retrieval, and document candidates separately; publishes only the approved combination; generates the next exploration suite; replays the old checkpoint with the old manifest; forks it with the candidate; and rolls production back without deleting history.

- [ ] **Step 2: Run the new test after Tasks 1–20**

Run: `pytest tests/integration/test_knowledge_iteration_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 3: Document operations and recovery**

Document Web and CLI start, approval, canary, rollback, worker lease recovery, artifact retention, feature-flag rollout, and PostgreSQL backup requirements. Include exact API/CLI examples and the rule that new exploration scores never directly trigger rollback.

- [ ] **Step 4: Run final verification**

Run:

```bash
git diff --check
pytest tests/unit tests/integration -q
pytest tests/test_deepeval_agent.py tests/test_deepeval_risk.py -q
cd console && npm test && npm run build
```

Expected: no whitespace errors; all Python and console tests PASS.

- [ ] **Step 5: Enable one canary Agent and verify rollback**

Set `feature_flags_json["knowledge_iteration"] = true` for one non-production Agent, bootstrap its baseline release, run a 10-case fixed suite, publish a no-op candidate, verify the new exploration suite, and roll back to the baseline release. Confirm release events and checkpoint references remain queryable.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_knowledge_iteration_end_to_end.py README.md eval/README_DEEPEVAL.md docs/knowledge-iteration-ops.md
git commit -m "docs: add knowledge iteration rollout runbook"
```

## Rollback Points

- After Task 4: disable `knowledge_iteration`; legacy runtime remains the compatibility path.
- After Task 7: reports can continue exporting from files while DB persistence is repaired.
- After Task 12: candidate versions are unreachable from production until publication APIs exist.
- After Task 13: switch `agent_runtime_bindings` to the previous release; never delete the failed manifest.
- After Task 16: stop post-release generation jobs; fixed suites remain unchanged.
- After Tasks 17–20: hide the console route and continue operating through existing eval scripts.

## Plan Completion Criteria

- Every requirement and acceptance criterion in `prd.md` maps to at least one task above.
- Every database mutation is tenant-scoped and version-pinned.
- Every candidate is single-category, isolated, evaluated, and human-approved.
- Every release is reproducible through its manifest and LangGraph checkpoint.
- Every release can be rolled back without rewriting history.
- Every published release produces a next-round exploration suite without changing the suite that approved it.
