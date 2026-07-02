# Iteration Observability and Effect Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable iteration events, reproducible candidate/final effect reports, latest-ten trends, and consistent API/Web/CLI presentation.

**Architecture:** Optimization state changes and append-only events share PostgreSQL transactions. Existing leased jobs trigger a report service that reads pinned evaluation rows, computes a versioned formula, persists normalized report data, and renders deterministic artifacts; all clients consume the same Agent-scoped API.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy asyncio, Alembic, PostgreSQL, LangGraph, DeepEval persistence, Typer, React 18, TypeScript, Ant Design, SSE, pytest, Vitest.

---

## File map

- `src/sales_agent/models/iteration_observability.py`: event/report/metric/case models.
- `src/sales_agent/migrations/versions/0008_iteration_observability_reports.py`: schema and indexes.
- `src/sales_agent/optimization/event_service.py`: sequence allocation, redaction, replay, wait.
- `src/sales_agent/optimization/reporting/formula.py`: versioned metric registry and score math.
- `src/sales_agent/optimization/reporting/types.py`: immutable report document types.
- `src/sales_agent/optimization/reporting/service.py`: DB aggregation and idempotent persistence.
- `src/sales_agent/optimization/reporting/renderers.py`: JSON/Markdown/HTML/CSV projections.
- `src/sales_agent/optimization/reporting/artifacts.py`: atomic artifact storage/hashing.
- `src/sales_agent/api/optimization_schemas.py`: API contracts.
- `src/sales_agent/api/routes/optimization.py`: events, wait/SSE, reports, trends.
- `src/sales_agent/optimization/worker.py`: report/post-publish lifecycle jobs.
- `src/sales_agent/cli_optimization.py`: watch and report export.
- `console/src/api/optimization.ts`: typed client.
- `console/src/pages/Agents/KnowledgeIterationPage.tsx`: workspace composition.
- `console/src/pages/Agents/optimization/*`: focused progress/report components.

## Task 1: Add event and report persistence

**Files:**
- Create: `src/sales_agent/models/iteration_observability.py`
- Create: `src/sales_agent/migrations/versions/0008_iteration_observability_reports.py`
- Modify: `src/sales_agent/models/optimization.py`
- Modify: `src/sales_agent/models/__init__.py`
- Test: `tests/unit/optimization/test_observability_models.py`
- Test: `tests/unit/test_entrypoint_migration.py`

- [ ] **Step 1: Write the failing metadata tests**

```python
def test_observability_tables_and_tenant_keys():
    expected = {"iteration_events", "iteration_reports", "iteration_report_metrics", "iteration_report_cases"}
    assert expected <= set(Base.metadata.tables)
    for name in expected:
        assert "tenant_id" in Base.metadata.tables[name].c

def test_iteration_has_atomic_event_cursor_and_final_report_refs():
    columns = Base.metadata.tables["optimization_iterations"].c
    assert {"event_sequence", "current_stage", "post_publish_eval_run_id", "final_report_id"} <= set(columns)
```

- [ ] **Step 2: Confirm the tests fail**

Run: `pytest tests/unit/optimization/test_observability_models.py -q`

Expected: FAIL because the four tables and iteration columns are absent.

- [ ] **Step 3: Implement the models and migration**

Use a non-null `candidate_key` (`candidate ID` or `"__final__"`) in the report uniqueness boundary:

```python
class IterationReport(TimestampMixin, Base):
    __tablename__ = "iteration_reports"
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(Text)
    candidate_key: Mapped[str] = mapped_column(Text, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="generating")
    data_snapshot_hash: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("tenant_id", "iteration_id", "report_type", "candidate_key", "report_version", name="uq_iteration_report_version"),
        Index("ix_iteration_reports_tenant_agent", "tenant_id", "agent_id", "created_at"),
    )
```

Add all fields defined in `design.md`; store JSON as `Text` consistently with current models. Migration revision is `0008_iteration_observability_reports`, down revision `0007_knowledge_facts`, and downgrade removes only the new schema additions.

- [ ] **Step 4: Verify metadata and migration**

Run: `pytest tests/unit/optimization/test_observability_models.py tests/unit/test_entrypoint_migration.py -q && alembic upgrade head && alembic current`

Expected: tests PASS and current revision is `0008_iteration_observability_reports`.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/models src/sales_agent/migrations/versions/0008_iteration_observability_reports.py tests/unit
git commit -m "feat: persist iteration events and effect reports"
```

## Task 2: Implement transactional events and replay APIs

**Files:**
- Create: `src/sales_agent/optimization/event_service.py`
- Modify: `src/sales_agent/api/optimization_schemas.py`
- Modify: `src/sales_agent/api/routes/optimization.py`
- Test: `tests/unit/optimization/test_event_service.py`
- Test: `tests/integration/test_optimization_events_api.py`

- [ ] **Step 1: Write sequence, rollback, redaction, and cursor tests**

```python
@pytest.mark.asyncio
async def test_append_allocates_monotonic_sequence(db_session, iteration):
    service = IterationEventService(db_session)
    first = await service.append(iteration, "stage.started", stage="diagnosing")
    second = await service.append(iteration, "stage.completed", stage="diagnosing")
    assert [first.sequence_no, second.sequence_no] == [1, 2]

@pytest.mark.asyncio
async def test_event_payload_redacts_secrets(db_session, iteration):
    event = await IterationEventService(db_session).append(
        iteration, "stage.progress", payload={"token": "secret", "candidate_id": "c1"}
    )
    assert json.loads(event.payload_json) == {"token": "[REDACTED]", "candidate_id": "c1"}
```

Add a PostgreSQL integration test with two sessions appending concurrently and asserting unique sorted sequences. Add an API test that requests `after_sequence=1` and receives only later events.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/test_event_service.py tests/integration/test_optimization_events_api.py -q`

Expected: FAIL on missing service and routes.

- [ ] **Step 3: Implement the event service**

```python
async def append(
    self,
    iteration: OptimizationIteration,
    event_type: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    progress: tuple[int, int] | None = None,
    message: str = "",
    payload: Mapping[str, Any] | None = None,
    actor: EventActor = EventActor.system(),
) -> IterationEvent:
    sequence = await self.db.scalar(
        update(OptimizationIteration)
        .where(
            OptimizationIteration.id == iteration.id,
            OptimizationIteration.tenant_id == iteration.tenant_id,
        )
        .values(event_sequence=OptimizationIteration.event_sequence + 1)
        .returning(OptimizationIteration.event_sequence)
    )
    event = IterationEvent(sequence_no=sequence, tenant_id=iteration.tenant_id, agent_id=iteration.agent_id, iteration_id=iteration.id, ...)
    self.db.add(event)
    await self.db.flush()
    return event
```

Provide `list_after()` and `wait_after()`; `wait_after()` uses short async polling with a monotonic deadline and never commits the caller session.

- [ ] **Step 4: Add replay, wait, and SSE routes**

Return `EventPage(events, next_sequence, terminal)`. SSE parses `Last-Event-ID`, emits `id: <sequence>` and JSON `data`, heartbeat comments every 15 seconds, and closes after terminal delivery. Bound `limit` to 1–200 and timeout to 1–30 seconds.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/unit/optimization/test_event_service.py tests/integration/test_optimization_events_api.py -q`

Expected: PASS, including concurrent PostgreSQL sequencing when integration DB is configured.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/optimization/event_service.py src/sales_agent/api tests/unit/optimization/test_event_service.py tests/integration/test_optimization_events_api.py
git commit -m "feat: stream durable optimization events"
```

## Task 3: Implement the versioned effect formula and report aggregation

**Files:**
- Create: `src/sales_agent/optimization/reporting/__init__.py`
- Create: `src/sales_agent/optimization/reporting/types.py`
- Create: `src/sales_agent/optimization/reporting/formula.py`
- Create: `src/sales_agent/optimization/reporting/service.py`
- Test: `tests/unit/optimization/reporting/test_formula.py`
- Test: `tests/unit/optimization/reporting/test_report_service.py`

- [ ] **Step 1: Write formula and hard-gate tests**

```python
def test_effect_v1_weights_sum_to_one():
    assert sum(EFFECT_V1.group_weights.values()) == pytest.approx(1.0)

def test_lower_is_better_is_normalized_before_delta():
    metric = MetricDefinition("p95_latency_ms", "efficiency", direction="lower", minimum=0, maximum=5000)
    assert metric.normalize(1000) > metric.normalize(2000)

def test_hard_gate_overrides_positive_composite():
    decision = EFFECT_V1.decide(report_type="candidate", delta=12.0, gates={"tenant_leakage": False})
    assert decision.recommendation == "do_not_publish"
```

Write service fixtures containing baseline/candidate `EvalMetricResult`, `EvalRunResult`, and retrieval hits, then assert deterministic report rows and case classifications.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/reporting -q`

Expected: FAIL because reporting modules do not exist.

- [ ] **Step 3: Implement typed report inputs and formula registry**

```python
@dataclass(frozen=True)
class MetricDefinition:
    name: str
    group: str
    aliases: tuple[str, ...]
    aggregation: Literal["mean", "pass_rate", "p95", "rate"]
    direction: Literal["higher", "lower"]
    minimum: float
    maximum: float
    hard_gate: str | None = None

@dataclass(frozen=True)
class EffectFormula:
    version: str
    group_weights: Mapping[str, float]
    metrics: tuple[MetricDefinition, ...]
```

Use exact v1 group weights from the PRD. Exclude `not_applicable` and `invalid` from score numerators and expose coverage counts.

- [ ] **Step 4: Implement idempotent report aggregation**

`generate_candidate()` requires baseline and candidate eval IDs. `generate_final()` requires baseline and post-publish eval IDs plus release ID. Both validate matching tenant/Agent/iteration, load results in stable case/metric order, calculate canonical input JSON and SHA-256, and return the existing completed report when hash/formula match.

Case classification rules are deterministic: error if comparison errored; improved/regressed from pass transition first, then normalized score tolerance; new when no baseline match; otherwise unchanged.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/optimization/reporting -q`

Expected: PASS with effect score, coverage, gates, classifications, and hashes stable across row order.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/optimization/reporting tests/unit/optimization/reporting
git commit -m "feat: compute reproducible iteration effect reports"
```

## Task 4: Render and serve report artifacts and trends

**Files:**
- Create: `src/sales_agent/optimization/reporting/renderers.py`
- Create: `src/sales_agent/optimization/reporting/artifacts.py`
- Modify: `src/sales_agent/optimization/reporting/service.py`
- Modify: `src/sales_agent/api/optimization_schemas.py`
- Modify: `src/sales_agent/api/routes/optimization.py`
- Test: `tests/unit/optimization/reporting/test_renderers.py`
- Test: `tests/integration/test_optimization_reports_api.py`

- [ ] **Step 1: Write deterministic projection and tenant-isolation tests**

```python
def test_all_renderers_share_report_identity(report_document, tmp_path):
    artifacts = ArtifactWriter(tmp_path).write_all(report_document)
    assert json.loads(artifacts.json.path.read_text())["report_id"] == report_document.report_id
    assert report_document.report_id in artifacts.markdown.path.read_text()
    assert report_document.report_id in artifacts.html.path.read_text()

@pytest.mark.asyncio
async def test_trend_excludes_candidate_and_other_tenant(api_client, seeded_reports):
    response = await api_client.get("/agents/a1/optimization/trends?limit=10")
    assert {item["report_type"] for item in response.json()} == {"final"}
    assert all(item["tenant_id"] == "t1" for item in response.json())
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/reporting/test_renderers.py tests/integration/test_optimization_reports_api.py -q`

Expected: FAIL on missing writer/routes.

- [ ] **Step 3: Implement atomic artifacts**

Write under `eval/results/iterations/{tenant}/{agent}/{iteration}/{report}/`, first to `.tmp`, `fsync`, hash SHA-256, then `os.replace`. JSON uses sorted keys; CSV has one metric/case record type column; HTML escapes all user content and embeds only authoritative JSON.

- [ ] **Step 4: Implement report and trend endpoints**

Every lookup starts with `_get_agent()` and tenant-scoped report predicates. Artifact endpoints resolve only stored paths beneath the configured report root and set content type/disposition. Trend reads latest completed `final` reports ordered by iteration number and caps limit at ten.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/optimization/reporting tests/integration/test_optimization_reports_api.py -q`

Expected: PASS and all artifact hashes match bytes on disk.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/optimization/reporting src/sales_agent/api tests
git commit -m "feat: export and serve iteration reports"
```

## Task 5: Wire post-publish evaluation and report jobs into the worker

**Files:**
- Modify: `src/sales_agent/optimization/worker.py`
- Modify: `src/sales_agent/optimization/nodes.py`
- Modify: `src/sales_agent/optimization/graph.py`
- Modify: `src/sales_agent/api/routes/optimization.py`
- Test: `tests/unit/optimization/test_report_lifecycle.py`
- Test: `tests/integration/test_post_publish_iteration.py`

- [ ] **Step 1: Write lifecycle tests**

```python
@pytest.mark.asyncio
async def test_publish_does_not_complete_before_post_publish_report(harness):
    iteration = await harness.publish_candidate()
    assert iteration.status == "post_publish_evaluating"
    await harness.complete_post_publish_fixed_eval()
    await harness.run_report_job()
    assert harness.iteration.status == "completed"
    assert harness.iteration.final_report_id is not None

@pytest.mark.asyncio
async def test_failed_final_gate_emits_rollback_recommendation(harness):
    await harness.complete_post_publish_fixed_eval(tenant_leakage=True)
    report = await harness.run_report_job()
    assert report.recommendation == "rollback_recommended"
    assert "rollback.recommended" in harness.event_types
```

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/optimization/test_report_lifecycle.py tests/integration/test_post_publish_iteration.py -q`

Expected: FAIL because publish currently switches the binding without post-publish report lifecycle.

- [ ] **Step 3: Add concrete worker handlers**

Add `post_publish_eval` and `generate_report` handlers. `post_publish_eval` invokes the existing persisted eval runner with the published release and fixed suite; it writes `post_publish_eval_run_id` and queues the report job in the same transaction. `generate_report` invokes `IterationReportService`, updates `final_report_id`, emits `final_report_ready`, then sets `completed` only for a completed final report.

Use `IterationEventService` for every lifecycle mutation. Preserve checkpoint IDs and pinned release IDs in job payloads. Do not make report handlers call publish or rollback.

- [ ] **Step 4: Run lifecycle and parent optimization tests**

Run: `pytest tests/unit/optimization tests/integration/test_post_publish_iteration.py -q`

Expected: PASS; no existing candidate gate tests regress.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/optimization src/sales_agent/api/routes/optimization.py tests
git commit -m "feat: close iteration with post-publish reporting"
```

## Task 6: Add CLI watch and report export

**Files:**
- Modify: `src/sales_agent/cli_optimization.py`
- Test: `tests/unit/test_optimization_cli.py`

- [ ] **Step 1: Write CLI request/cursor tests**

Add tests that monkeypatch `_fetch`, assert `watch` advances `after_sequence`, stops on terminal, and `report --format markdown --output path` writes exactly the API artifact bytes. Assert supported formats are JSON/Markdown/HTML/CSV.

- [ ] **Step 2: Confirm failures**

Run: `pytest tests/unit/test_optimization_cli.py -q`

Expected: FAIL because watch is one-shot and report command is absent.

- [ ] **Step 3: Implement synchronous HTTP helpers and commands**

Correct `_fetch` to be synchronous or execute an async implementation explicitly; current commands must never print coroutine objects. Add `watch --after-sequence --timeout --json` and `report --report-id --format --output`. Preserve Ctrl-C with the last printed sequence.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_optimization_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/cli_optimization.py tests/unit/test_optimization_cli.py
git commit -m "feat: watch and export optimization reports from CLI"
```

## Task 7: Build the Web progress and report workspace

**Files:**
- Modify: `console/src/api/optimization.ts`
- Create: `console/src/pages/Agents/optimization/IterationProgressPanel.tsx`
- Create: `console/src/pages/Agents/optimization/EffectReportPanel.tsx`
- Create: `console/src/pages/Agents/optimization/RegressionCasesTable.tsx`
- Create: `console/src/pages/Agents/optimization/IterationTrendChart.tsx`
- Modify: `console/src/pages/Agents/KnowledgeIterationPage.tsx`
- Test: `console/src/tests/api/optimization.test.ts`
- Test: `console/src/tests/pages/KnowledgeIterationPage.test.tsx`

- [ ] **Step 1: Write API and component tests**

Mock event replay/SSE, report, and trend endpoints. Assert reconnect uses the last event sequence; a hard gate renders a red blocking state; candidate and final report labels differ; regression rows sort first; trend accepts only final reports.

- [ ] **Step 2: Confirm failures**

Run: `cd console && npm test -- --run src/tests/api/optimization.test.ts src/tests/pages/KnowledgeIterationPage.test.tsx`

Expected: FAIL because contracts/components are absent.

- [ ] **Step 3: Add typed API contracts and focused components**

Define `IterationEvent`, `EventPage`, `IterationReportSummary`, `IterationReportDocument`, `ReportMetric`, `ReportCase`, and `TrendPoint`. Keep state in the page for selected iteration/report and let each component render one responsibility. Use Ant Design Progress, Timeline, Statistic, Alert, Table, and a lightweight SVG trend line rather than adding a chart dependency.

- [ ] **Step 4: Connect live updates with replay fallback**

Open one `EventSource` for a running selected iteration. On error, close it, call replay with the last sequence, and reconnect with bounded exponential backoff. On `candidate.report_ready` or `final_report_ready`, invalidate report/trend queries. Close on unmount or terminal state.

- [ ] **Step 5: Run frontend verification**

Run: `cd console && npm test -- --run src/tests/api/optimization.test.ts src/tests/pages/KnowledgeIterationPage.test.tsx && npm run build`

Expected: tests PASS and TypeScript/Vite build succeeds.

- [ ] **Step 6: Commit**

```bash
git add console/src/api/optimization.ts console/src/pages/Agents console/src/tests
git commit -m "feat: visualize optimization progress and effects"
```

## Task 8: Full verification and operations documentation

**Files:**
- Modify: `docs/knowledge-iteration-ops.md`
- Modify: `.trellis/tasks/07-02-iteration-observability-reports/check.jsonl`

- [ ] **Step 1: Document operations**

Document migration order, report root and retention, status meanings, SSE/long-poll examples, report rebuild command, final hard-gate response, time-travel lineage, and rollback procedure. State explicitly that publish is not completion.

- [ ] **Step 2: Run backend verification**

Run: `pytest tests/unit/optimization tests/unit/test_optimization_cli.py tests/integration/test_optimization_events_api.py tests/integration/test_optimization_reports_api.py tests/integration/test_post_publish_iteration.py -q`

Expected: PASS.

- [ ] **Step 3: Run migration and frontend verification**

Run: `alembic upgrade head && alembic current && cd console && npm test -- --run && npm run build`

Expected: Alembic at `0008_iteration_observability_reports`; all frontend tests and build PASS.

- [ ] **Step 4: Run task validation and inspect diff**

Run: `python3 ./.trellis/scripts/task.py validate 07-02-iteration-observability-reports && git diff --check && git status --short`

Expected: task validation and diff check succeed; only intended files remain.

- [ ] **Step 5: Commit**

```bash
git add docs/knowledge-iteration-ops.md .trellis/tasks/07-02-iteration-observability-reports
git commit -m "docs: add iteration observability operations"
```
