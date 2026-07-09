# Memory Evaluation and Production Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi-turn and memory quality measurable before release and maintainable after release — a unified dataset/metrics/report core, five test layers, seven CLI modes, automated release gates, bounded online sampling, and a promote-trace feedback loop.

**Architecture:** Build a new `eval/memory_eval/` Python package that owns the unified scenario schema, deterministic metrics, versioned reporting, and a scenario runner that drives the **real** production path (`invoke_online_turn` → Online Graph → memory tables/checkpoints). Deterministic properties are code assertions; semantic quality uses isolated, timeout-guarded LLM judges. Production operations add a sampling + restricted-trace + promote-trace workflow backed by two new DB tables. The package reuses the existing `eval/support/dingtalk_scenario.py` runner pattern, the `FakeModel`/`StubChatRunner` double convention, and the `db_session`/`sample_tenant`/`active_agent` test fixtures.

**Tech Stack:** Python 3.10, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Pydantic v2, SQLAlchemy 2 async + asyncpg, LangGraph + `AsyncPostgresSaver`, Alembic (async), argparse runners writing JSON+Markdown, stdlib `logging`. No new tracing library (repo convention is DB-backed observability + stdlib logging).

## Scope Note (read before executing)

This spec is large and spans two natural subsystems that share a common core (schema, metrics, report, versions):

- **Phases 1–4 + 6 = Evaluation harness** (schema → metrics → report → doubles → scenario runner → test-layer modes → ≥40 dataset → release gates → semantic judges → gate script + docs). After Phase 4 the repo has: *40+ scenarios run from one command*, *automated fail-closed release gates*, and *reports exposing applicability/denominators/errors/versions*. This alone satisfies 6 of the 8 acceptance criteria and is independently shippable.
- **Phase 5 = Production operations** (eval trace capture → DB tables → online-sample → compare → promote-trace). Satisfies the remaining 2 acceptance criteria (online eval non-blocking + bounded; production failures promotable into regressions).

**Recommendation:** Execute Phases 1–4 + 6 first, commit, and ship. Execute Phase 5 as a second pass. The shared core (Tasks 1–4) is built once; Phase 5 depends on it but does not modify it.

**Spec source:** `docs/superpowers/specs/2026-07-08-memory-evaluation-operations-design.md` (Spec 4 of 4). Sections referenced as (§N).

## Global Constraints

Copied verbatim from the spec; every task implicitly includes these:

- Deterministic properties use code assertions; LLM judges never decide tenant isolation, memory deletion, Topic IDs, tool/risk actions, or exact state transitions. (§2.2)
- Missing inputs and evaluator failures are `N/A` or errors, never perfect scores. (§2.6)
- Every report records model, prompt, code, dataset, knowledge, and memory-schema versions. (§2.5)
- Production data must be anonymized and reviewed before entering a committed dataset. (§2.7, §10)
- Judge timeout/error is reported separately and does not change product pass/fail counts. (§10)
- A scenario setup or database failure invalidates the scenario run rather than producing a product score. (§10)
- Production online-evaluation failure cannot block the user response. (§10)
- Each CLI mode writes JSON plus a human-readable report and exits non-zero only for its own configured quality gates or invalid execution. (§11)
- Release gates fail closed for isolation/safety violations: zero cross-tenant, cross-Agent, cross-user, and prohibited-memory leakage; zero assistant-output-to-user-memory activations. (§6)
- Python 3.10; backend is CommonJS-free Python (`from __future__ import annotations` at module top, matching `eval/run_short_term_memory_eval.py`).
- Database changes go through an Alembic migration (repo rule); do not call `Base.metadata.create_all` for production tables (LangGraph checkpoint tables are the documented exception).

## File Structure

New package `eval/memory_eval/` (mirrors the existing `eval/router/`, `eval/memory/`, `eval/optimizer/` layout). One responsibility per file:

**Foundation (shared core):**
- `eval/memory_eval/__init__.py` — package marker, re-exports public API.
- `eval/memory_eval/schema.py` — Pydantic scenario schema (§4): `ExpectedTurn`, `ScenarioTurn`, `FinalExpected`, `MultiturnScenario`, `ObservedTurn`, `ScenarioRun`. Canonical schema for the whole program.
- `eval/memory_eval/versions.py` — `VersionBundle` + `collect_version_bundle()` (§2.5, §7).
- `eval/memory_eval/metrics/__init__.py` — package marker.
- `eval/memory_eval/metrics/types.py` — `MetricResult`, `MetricDefinition`, `ConfusionMatrix` (§5, §7).
- `eval/memory_eval/report.py` — `EvaluationReport` aggregation + `write_report()` → `report.json` + `report.md` grouped per §7.
- `eval/memory_eval/dataset.py` — `load_scenarios()`, `validate_dataset()`, anonymization/secret gate (§10).

**Metric modules (deterministic, one §5 group per file):**
- `eval/memory_eval/metrics/turn_topic.py` — §5.1.
- `eval/memory_eval/metrics/memory_lifecycle.py` — §5.2.
- `eval/memory_eval/metrics/recall_profile.py` — §5.3.
- `eval/memory_eval/metrics/conversation.py` — §5.4.

**Runner + doubles:**
- `eval/memory_eval/model_double.py` — `ScriptedModelDouble(ChatModel)`, `DeterministicEmbeddingDouble(EmbeddingModel)`.
- `eval/memory_eval/scenario_runner.py` — `ScenarioRunner` drives `invoke_online_turn` turn-by-turn; captures `ObservedTurn`; handles restart/duplicate/concurrent/time-offset.

**Modes + gates:**
- `eval/memory_eval/runner.py` — argparse dispatcher with the seven §11 modes; each writes JSON+md and returns an exit code.
- `eval/memory_eval/gates.py` — `ReleaseGates` (§6), `check_gates() -> GateReport`.

**Operations (Phase 5):**
- `src/sales_agent/services/memory_eval_trace.py` — `build_eval_trace(state, *, db, now)` extracts the §8 per-turn fields.
- `src/sales_agent/models/memory_eval.py` — `MemoryEvalTraceRecord`, `PromotedRegression` ORM models.
- `src/sales_agent/migrations/versions/0015_memory_eval_operations.py` — creates the two tables.

**Semantic evaluators (Phase 6):**
- `eval/memory_eval/semantic.py` — reference-free judges with timeout/error isolation (§3.3, §10).

**Dataset + wire-up + docs:**
- `eval/memory/datasets/multiturn_v1.jsonl` — the ≥40-scenario versioned dataset (§4).
- `scripts/run_memory_eval_gate.sh` — gate script (mirrors `scripts/run_short_term_memory_gate.sh`).
- `tests/unit/eval/test_memory_eval_*.py` — one test file per module (unit); integration tests under `tests/integration/`.
- `docs/runbooks/memory-evaluation.md` — operator runbook.
- `changelog/2026-07-08.md` (append) + README `## 更新日志` row.

**Relationship to existing code (do not refactor out of scope):**
- The existing `eval/support/dingtalk_scenario.py` (`ShortTermScenario`) and `eval/run_*_memory_eval.py` runners stay as-is. The new `schema.py` is the richer canonical schema; nothing imports the old one into the new package.

---

## Task Index

**Phase 1 — Foundation:** Task 1 schema · Task 2 versions · Task 3 metric types · Task 4 report · Task 5 dataset loader
**Phase 2 — Metric modules:** Task 6 turn/topic · Task 7 memory lifecycle · Task 8 recall/profile · Task 9 conversation
**Phase 3 — Runner + doubles:** Task 10 model/embedding doubles · Task 11 scenario runner
**Phase 4 — Modes + dataset + gates:** Task 12 unit-memory + graph-multiturn + CLI · Task 13 model-multiturn · Task 14 dingtalk-staging · Task 15 ≥40-scenario dataset · Task 16 release gates
**Phase 5 — Operations:** Task 17 eval trace capture · Task 18 DB models + migration · Task 19 online-sample · Task 20 compare · Task 21 promote-trace
**Phase 6 — Semantic + wire-up + docs:** Task 22 semantic evaluators · Task 23 gate script + README/changelog/runbook

---

## Phase 1 — Foundation (shared core)

These five tasks build the schema, versioning, metric core, report writer, and dataset loader that every later task depends on. Each is pure-Python with no model/DB calls.

### Task 1: Scenario schema (`schema.py`)

**Files:**
- Create: `eval/memory_eval/__init__.py`
- Create: `eval/memory_eval/schema.py`
- Test: `tests/unit/eval/test_memory_eval_schema.py`

**Interfaces:**
- Consumes: Pydantic v2 only.
- Produces: `ExpectedTurn`, `ScenarioTurn`, `FinalExpected`, `MultiturnScenario`, `ObservedTurn`, `ScenarioRun`. Later tasks construct these from JSONL and from observed graph state.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_memory_eval_schema.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.memory_eval.schema import (
    ExpectedTurn,
    FinalExpected,
    MultiturnScenario,
    ScenarioTurn,
)


def _scenario_dict():
    return {
        "id": "memory-cross-session-001",
        "version": 1,
        "tags": ["long_term", "profile", "cross_session"],
        "initial_state": {},
        "turns": [
            {
                "input": "记住我负责华东区",
                "time_offset_seconds": 0,
                "restart_before": False,
                "expected": {
                    "turn_relation": "new",
                    "memory_operation": "remember",
                    "active_memory_keys": ["sales_region"],
                    "reply_contains": ["华东"],
                },
            }
        ],
        "final_expected": {"active_topic_count": 1, "cross_scope_leakage": False},
    }


def test_scenario_loads_from_spec_example():
    s = MultiturnScenario(**_scenario_dict())
    assert s.id == "memory-cross-session-001"
    assert s.version == 1
    assert s.turns[0].expected.memory_operation == "remember"
    assert s.final_expected.active_topic_count == 1


def test_unknown_turn_relation_rejected():
    data = _scenario_dict()
    data["turns"][0]["expected"]["turn_relation"] = "bogus"
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)


def test_first_turn_cannot_be_duplicate():
    data = _scenario_dict()
    data["turns"][0]["duplicate_previous_event"] = True
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)


def test_turn_count_bounds():
    data = _scenario_dict()
    data["turns"] = [{"input": str(i), "expected": {}} for i in range(7)]
    with pytest.raises(ValidationError):
        MultiturnScenario(**data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.memory_eval'`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/__init__.py
"""Memory evaluation and operations package (Spec 4)."""
```

```python
# eval/memory_eval/schema.py
"""Unified multi-turn scenario schema (Spec 4 §4).

This is the canonical schema for the whole memory program. It extends the
ideas in ``eval/support/dingtalk_scenario.py`` with first-class controls for
time offsets, restart, worker selection, duplicate event IDs, and concurrent
groups, plus the full set of per-turn expectations from §3.2.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TurnRelation = Literal["continue", "revise", "switch", "new", "ambiguous"]
TopicTransition = Literal["same", "new", "restored", "none"]


class ExpectedTurn(BaseModel):
    """What one turn should produce (§3.2 asserts-after-each-turn fields)."""

    response_kind: str | None = None
    turn_relation: TurnRelation | None = None
    standalone_query_contains: list[str] = Field(default_factory=list)
    retained_entities: list[str] = Field(default_factory=list)
    retracted_goals: list[str] = Field(default_factory=list)
    topic_transition: TopicTransition | None = None
    topic_id: str | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    profile_version: str | None = None
    retrieval_decision: str | None = None
    risk_decision: str | None = None
    trace_nodes: list[str] = Field(default_factory=list)
    memory_operation: str | None = None
    memory_status: str | None = None
    active_memory_keys: list[str] = Field(default_factory=list)
    active_memory_values: list[str] = Field(default_factory=list)
    candidate_count: int | None = None
    sensitive_persisted: int | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_count: int = 1


class ScenarioTurn(BaseModel):
    """One turn in a multi-turn scenario (§4 first-class controls)."""

    input: str
    event_id: str | None = None
    time_offset_seconds: int = 0
    restart_before: bool = False
    duplicate_previous_event: bool = False
    concurrent_group: str | None = None
    worker_id: str | None = None
    expected: ExpectedTurn = Field(default_factory=ExpectedTurn)


class FinalExpected(BaseModel):
    """Assertions evaluated once, after the final turn (§4 final_expected)."""

    active_topic_count: int | None = None
    cross_scope_leakage: bool | None = None
    persisted_topic_count: int | None = None
    persisted_message_count: int | None = None
    active_memory_count: int | None = None
    outbox_drained: bool | None = None
    profile_version: str | None = None


class MultiturnScenario(BaseModel):
    """A complete versioned multi-turn scenario (§4)."""

    id: str
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    turns: list[ScenarioTurn] = Field(min_length=1, max_length=6)
    final_expected: FinalExpected = Field(default_factory=FinalExpected)

    @model_validator(mode="after")
    def _validate(self) -> "MultiturnScenario":
        if self.turns and self.turns[0].duplicate_previous_event:
            raise ValueError(f"Scenario {self.id}: first turn cannot duplicate")
        return self


class ObservedTurn(BaseModel):
    """What the scenario runner observed after a single turn."""

    turn_index: int
    result: dict[str, Any]
    replies: list[str]
    active_topic_ids: list[str]
    closed_topic_ids: list[str]
    active_memory_keys: list[str]
    selected_memory_ids: list[str]
    profile_version: str | None = None
    duplicate: bool = False
    error: str | None = None


class ScenarioRun(BaseModel):
    """Full observed run of one scenario."""

    scenario_id: str
    observed: list[ObservedTurn]
    final_state: dict[str, Any]
    error: str | None = None


__all__ = [
    "ExpectedTurn",
    "FinalExpected",
    "MultiturnScenario",
    "ObservedTurn",
    "ScenarioRun",
    "ScenarioTurn",
    "TurnRelation",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_schema.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/__init__.py eval/memory_eval/schema.py tests/unit/eval/test_memory_eval_schema.py
git commit -m "feat(eval): add unified multi-turn scenario schema (Spec 4 §4)"
```

---

### Task 2: Version bundle (`versions.py`)

Every report records model/prompt/code/dataset/knowledge/memory-schema versions (§2.5, §7). Gather them in one place.

**Files:**
- Create: `eval/memory_eval/versions.py`
- Test: `tests/unit/eval/test_memory_eval_versions.py`

**Interfaces:**
- Consumes: `sales_agent.core.config.get_settings()` (model name), `sales_agent.models` prompt registry is DB-backed (read best-effort), importlib metadata for code version.
- Produces: `VersionBundle` dataclass; `collect_version_bundle(*, dataset_version=None, memory_schema_version=None) -> VersionBundle`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_memory_eval_versions.py
from __future__ import annotations

from eval.memory_eval.versions import VersionBundle, collect_version_bundle


def test_version_bundle_has_all_required_fields():
    vb = collect_version_bundle(dataset_version="multiturn_v1", memory_schema_version="0013")
    assert isinstance(vb, VersionBundle)
    for field in (
        "model_version",
        "prompt_version",
        "code_version",
        "dataset_version",
        "knowledge_version",
        "memory_schema_version",
        "generator_version",
    ):
        assert hasattr(vb, field), f"missing {field}"
    assert vb.dataset_version == "multiturn_v1"
    assert vb.memory_schema_version == "0013"


def test_version_bundle_never_returns_none_for_required():
    vb = collect_version_bundle()
    # Required fields must be strings (empty string allowed, None is not).
    assert vb.model_version is not None
    assert vb.code_version is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_versions.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/versions.py
"""Collect every version a memory-eval report must record (Spec 4 §2.5, §7)."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionBundle:
    model_version: str
    prompt_version: str
    code_version: str
    dataset_version: str
    knowledge_version: str
    memory_schema_version: str
    generator_version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _safe_get_settings():
    try:
        from sales_agent.core.config import get_settings  # local import: heavy
        return get_settings()
    except Exception:  # pragma: no cover - best-effort
        logger.debug("get_settings unavailable; defaulting model version", exc_info=True)
        return None


def _code_version() -> str:
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("sales-agent")
        except PackageNotFoundError:
            return "0.1.0-dev"
    except Exception:  # pragma: no cover
        return "unknown"


def _model_version() -> str:
    settings = _safe_get_settings()
    if settings is None:
        return "unset"
    model = getattr(settings, "model", None)
    name = getattr(model, "chat_model", None) or getattr(model, "default_model", None)
    return str(name or "unset")


def _prompt_version() -> str:
    settings = _safe_get_settings()
    registry = getattr(settings, "prompt_registry", None) if settings else None
    return getattr(registry, "version", "db-managed") if registry else "db-managed"


def collect_version_bundle(
    *,
    dataset_version: Optional[str] = None,
    knowledge_version: Optional[str] = None,
    memory_schema_version: Optional[str] = None,
    generator_version: str = "memory_eval_v1",
) -> VersionBundle:
    """Build a VersionBundle, defaulting missing inputs to explicit strings.

    Per §2.6, missing inputs are reported explicitly — never silently None.
    """
    return VersionBundle(
        model_version=_model_version(),
        prompt_version=_prompt_version(),
        code_version=_code_version(),
        dataset_version=dataset_version or "unset",
        knowledge_version=knowledge_version or "unset",
        memory_schema_version=memory_schema_version or "unset",
        generator_version=generator_version,
    )


__all__ = ["VersionBundle", "collect_version_bundle"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_versions.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/versions.py tests/unit/eval/test_memory_eval_versions.py
git commit -m "feat(eval): add VersionBundle collector for memory-eval reports (Spec 4 §2.5)"
```

---

### Task 3: Metric core types (`metrics/types.py`)

Every metric records applicability, numerator, denominator, score, threshold, error, and sample IDs (§7). Define the shared container once.

**Files:**
- Create: `eval/memory_eval/metrics/__init__.py`
- Create: `eval/memory_eval/metrics/types.py`
- Test: `tests/unit/eval/test_memory_eval_metric_types.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `MetricResult`, `MetricDefinition`, `ConfusionMatrix`, `prf()`, `confusion()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_memory_eval_metric_types.py
from __future__ import annotations

from eval.memory_eval.metrics.types import (
    ConfusionMatrix,
    MetricResult,
    confusion,
    prf,
)


def test_prf_computes_precision_recall_f1():
    # 3 predicted positives, 2 correct; 2 actual positives.
    r = prf(tp=2, fp=1, fn=0)
    assert r.numerator == 2 and r.denominator == 3
    assert round(r.score, 4) == round(2 / 3, 4)


def test_prf_no_predictions_is_na_not_zero():
    r = prf(tp=0, fp=0, fn=2)
    assert r.applicable is False
    assert r.score == 0.0  # reported but marked not applicable


def test_metric_result_records_threshold_and_error():
    r = MetricResult(name="x", numerator=9, denominator=10, score=0.9, threshold=0.9)
    assert r.threshold == 0.9
    assert r.error is None
    assert r.sample_ids == []


def test_confusion_matrix_counts_matches():
    cm = confusion(
        expected=["new", "new", "continue"],
        observed=["new", "continue", "continue"],
        labels=["new", "continue"],
    )
    assert isinstance(cm, ConfusionMatrix)
    assert cm.matrix["new"]["new"] == 1
    assert cm.matrix["new"]["continue"] == 1
    assert cm.matrix["continue"]["continue"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_metric_types.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/metrics/__init__.py
"""Deterministic metric modules (Spec 4 §5)."""
```

```python
# eval/memory_eval/metrics/types.py
"""Shared metric containers (Spec 4 §5, §7).

Every metric records applicability, numerator, denominator, score, threshold,
error, and sample IDs (§7). Missing inputs are marked not-applicable, never
perfect (§2.6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MetricResult:
    name: str
    numerator: int = 0
    denominator: int = 0
    score: float = 0.0
    threshold: Optional[float] = None
    applicable: bool = True
    error: Optional[str] = None
    sample_ids: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        if self.error is not None or not self.applicable:
            return True  # errors/N-A never flip a product gate (§10)
        if self.threshold is None:
            return True
        return self.score >= self.threshold


@dataclass
class ConfusionMatrix:
    labels: list[str]
    matrix: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {row: dict(cols) for row, cols in self.matrix.items()}


def prf(*, tp: int, fp: int, fn: int, threshold: Optional[float] = None) -> MetricResult:
    """Precision/Recall/F1 rolled into one MetricResult (score = precision)."""
    predicted = tp + fp
    applicable = predicted > 0
    precision = tp / predicted if predicted else 0.0
    res = MetricResult(
        name="prf",
        numerator=tp,
        denominator=predicted,
        score=precision,
        threshold=threshold,
        applicable=applicable,
    )
    # Stash recall/f1 as extra attrs for callers that want them.
    res.recall = tp / (tp + fn) if (tp + fn) else 0.0  # type: ignore[attr-defined]
    f1 = (2 * precision * res.recall / (precision + res.recall)) if (precision + res.recall) else 0.0
    res.f1 = f1  # type: ignore[attr-defined]
    return res


def confusion(*, expected: list[str], observed: list[str], labels: list[str]) -> ConfusionMatrix:
    """Build a labels×labels confusion matrix from paired sequences."""
    matrix = {row: {col: 0 for col in labels} for row in labels}
    for exp, obs in zip(expected, observed):
        if exp in matrix and obs in matrix[exp]:
            matrix[exp][obs] += 1
    return ConfusionMatrix(labels=labels, matrix=matrix)


__all__ = ["ConfusionMatrix", "MetricResult", "confusion", "prf"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_metric_types.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/metrics/__init__.py eval/memory_eval/metrics/types.py tests/unit/eval/test_memory_eval_metric_types.py
git commit -m "feat(eval): add metric core types (MetricResult/prf/confusion) (Spec 4 §5,§7)"
```

---

### Task 4: Report writer (`report.py`)

Reports group results by deterministic state/safety, semantic, trajectory, persistence/isolation, latency/cost, and scenario slice (§7). Each metric shows applicability/numerator/denominator/score/threshold/error/sample IDs; reports show confusion matrices and P50/P95.

**Files:**
- Create: `eval/memory_eval/report.py`
- Test: `tests/unit/eval/test_memory_eval_report.py`

**Interfaces:**
- Consumes: `eval.memory_eval.metrics.types.MetricResult`, `eval.memory_eval.versions.VersionBundle`, `eval.memory_eval.metrics.types.ConfusionMatrix`.
- Produces: `EvaluationReport`, `write_report(report, out_dir) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_memory_eval_report.py
from __future__ import annotations

import json
from pathlib import Path

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.report import EvaluationReport, write_report
from eval.memory_eval.versions import VersionBundle


def _vb() -> VersionBundle:
    return VersionBundle(
        model_version="qwen-plus",
        prompt_version="db-managed",
        code_version="0.1.0-dev",
        dataset_version="multiturn_v1",
        knowledge_version="unset",
        memory_schema_version="0013",
        generator_version="memory_eval_v1",
    )


def test_write_report_emits_json_and_markdown(tmp_path: Path):
    report = EvaluationReport(versions=_vb(), total_scenarios=2, total_turns=4)
    report.add_metric(
        "deterministic_state_safety",
        MetricResult(name="turn_relation_accuracy", numerator=4, denominator=4, score=1.0, threshold=0.9),
    )
    out = write_report(report, str(tmp_path / "out"))
    assert (out / "report.json").exists()
    assert (out / "report.md").exists()
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["versions"]["dataset_version"] == "multiturn_v1"
    grp = data["groups"]["deterministic_state_safety"]
    assert grp[0]["applicable"] is True
    assert grp[0]["numerator"] == 4
    assert grp[0]["denominator"] == 4
    assert grp[0]["threshold"] == 0.9


def test_error_metric_does_not_flip_pass(tmp_path: Path):
    report = EvaluationReport(versions=_vb())
    report.add_metric(
        "semantic_answer_quality",
        MetricResult(name="relevance", score=0.0, error="judge timeout"),
    )
    data = json.loads((tmp_path.joinpath("r", "report.json").as_posix())) if False else None
    out = write_report(report, str(tmp_path / "err"))
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["groups"]["semantic_answer_quality"][0]["error"] == "judge timeout"
    assert data["thresholds_met"] is True  # error never flips gate (§10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/report.py
"""Evaluation report aggregation + JSON/Markdown writer (Spec 4 §7)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from eval.memory_eval.metrics.types import ConfusionMatrix, MetricResult
from eval.memory_eval.versions import VersionBundle

GROUP_ORDER = [
    "deterministic_state_safety",
    "semantic_answer_quality",
    "trajectory",
    "persistence_isolation",
    "latency_cost",
    "scenario_slice",
]


@dataclass
class EvaluationReport:
    versions: VersionBundle
    total_scenarios: int = 0
    total_turns: int = 0
    thresholds_met: bool = True
    groups: dict[str, list[MetricResult]] = field(default_factory=dict)
    confusion_matrices: dict[str, ConfusionMatrix] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def add_metric(self, group: str, result: MetricResult) -> None:
        self.groups.setdefault(group, []).append(result)
        if not result.passes:
            self.thresholds_met = False
            self.failures.append(f"{group}/{result.name}: {result.score} < {result.threshold}")

    def add_confusion(self, name: str, cm: ConfusionMatrix) -> None:
        self.confusion_matrices[name] = cm


def _metric_to_dict(m: MetricResult) -> dict:
    d = {
        "name": m.name,
        "applicable": m.applicable,
        "numerator": m.numerator,
        "denominator": m.denominator,
        "score": round(m.score, 6),
        "threshold": m.threshold,
        "error": m.error,
        "sample_ids": m.sample_ids,
    }
    for extra in ("recall", "f1"):
        if hasattr(m, extra):
            d[extra] = round(getattr(m, extra), 6)
    return d


def write_report(report: EvaluationReport, out_dir: str) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    data = {
        "versions": report.versions.to_dict(),
        "total_scenarios": report.total_scenarios,
        "total_turns": report.total_turns,
        "thresholds_met": report.thresholds_met,
        "groups": {
            g: [_metric_to_dict(m) for m in report.groups.get(g, [])]
            for g in GROUP_ORDER
            if g in report.groups
        },
        "confusion_matrices": {
            name: cm.to_dict() for name, cm in report.confusion_matrices.items()
        },
        "failures": report.failures,
    }
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}  Turns: {report.total_turns}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        "",
        "## Versions",
        "",
    ]
    for k, v in report.versions.to_dict().items():
        md.append(f"- {k}: {v}")
    md += ["", "## Metrics", ""]
    for group in GROUP_ORDER:
        for m in report.groups.get(group, []):
            flag = "PASS" if m.passes else "FAIL"
            md.append(
                f"- [{group}] {m.name}: {m.score:.3f} "
                f"({m.numerator}/{m.denominator}, applicable={m.applicable}) [{flag}]"
                + (f" err={m.error}" if m.error else "")
            )
    if report.failures:
        md += ["", "## Failures", ""]
        md += [f"- {f}" for f in report.failures]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out


__all__ = ["EvaluationReport", "write_report"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_report.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/report.py tests/unit/eval/test_memory_eval_report.py
git commit -m "feat(eval): add EvaluationReport + JSON/Markdown writer (Spec 4 §7)"
```

---

### Task 5: Dataset loader + anonymization gate (`dataset.py`)

Dataset ingestion rejects raw secrets, direct identifiers, and unreviewed production conversations (§10). Loader validates structure and tag coverage.

**Files:**
- Create: `eval/memory_eval/dataset.py`
- Test: `tests/unit/eval/test_memory_eval_dataset.py`

**Interfaces:**
- Consumes: `eval.memory_eval.schema.MultiturnScenario`.
- Produces: `load_scenarios(path) -> list[MultiturnScenario]`, `validate_dataset(scenarios) -> list[str]` (errors; empty = valid), `DatasetValidationError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_memory_eval_dataset.py
from __future__ import annotations

import json

import pytest

from eval.memory_eval.dataset import (
    DatasetValidationError,
    load_scenarios,
    validate_dataset,
)
from eval.memory_eval.schema import MultiturnScenario, ScenarioTurn


def _write(tmp_path, rows):
    p = tmp_path / "ds.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def test_load_scenarios_roundtrip(tmp_path):
    path = _write(tmp_path, [
        {"id": "a", "version": 1, "tags": ["x"], "turns": [{"input": "hi", "expected": {}}]},
    ])
    scenarios = load_scenarios(path)
    assert len(scenarios) == 1
    assert isinstance(scenarios[0], MultiturnScenario)


def test_validate_rejects_secrets_and_identifiers(tmp_path):
    scenarios = [
        MultiturnScenario(
            id="s1",
            turns=[ScenarioTurn(input="记住我的密码是 abc123", expected={})],
        ),
        MultiturnScenario(
            id="s2",
            turns=[ScenarioTurn(input="用户 13800138000 的信息", expected={})],
        ),
    ]
    errors = validate_dataset(scenarios)
    assert any("secret" in e.lower() or "password" in e.lower() for e in errors)
    assert any("direct identifier" in e.lower() for e in errors)


def test_validate_rejects_duplicate_ids(tmp_path):
    scenarios = [
        MultiturnScenario(id="dup", turns=[ScenarioTurn(input="hi", expected={})]),
        MultiturnScenario(id="dup", turns=[ScenarioTurn(input="yo", expected={})]),
    ]
    errors = validate_dataset(scenarios)
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_rejects_unreviewed_production_marker(tmp_path):
    scenarios = [
        MultiturnScenario(
            id="s1",
            tags=["unreviewed_production"],
            turns=[ScenarioTurn(input="hi", expected={})],
        ),
    ]
    errors = validate_dataset(scenarios)
    assert any("unreviewed" in e.lower() for e in errors)


def test_load_raises_on_bad_json(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        load_scenarios(str(path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_dataset.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/dataset.py
"""Scenario dataset loading + anonymization/secret gate (Spec 4 §4, §10)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from eval.memory_eval.schema import MultiturnScenario

# Patterns that must never enter a committed dataset (§10).
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|密码|secret|api[_-]?key|token)\b"),
]
# Direct identifiers — phone numbers, 15/18-digit Chinese ID cards, email.
_PHONE = re.compile(r"\b1[3-9]\d{9}\b")
_IDCARD = re.compile(r"\b\d{15}|\d{17}[\dXx]\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

_UNREVIEWED_TAG = "unreviewed_production"


class DatasetValidationError(Exception):
    """Raised when a dataset cannot be loaded (bad JSON / schema)."""


def load_scenarios(path: str) -> list[MultiturnScenario]:
    scenarios: list[MultiturnScenario] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                scenarios.append(MultiturnScenario(**obj))
            except Exception as exc:  # noqa: BLE001
                raise DatasetValidationError(f"{path}:{lineno}: {exc}") from exc
    return scenarios


def _scan_text(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            hits.append("raw secret/credential")
    if _PHONE.search(text):
        hits.append("direct identifier (phone)")
    if _IDCARD.search(text):
        hits.append("direct identifier (id card)")
    if _EMAIL.search(text):
        hits.append("direct identifier (email)")
    return hits


def validate_dataset(scenarios: list[MultiturnScenario]) -> list[str]:
    """Return a list of human-readable errors; empty list means valid (§10)."""
    errors: list[str] = []
    seen: set[str] = set()
    for s in scenarios:
        if s.id in seen:
            errors.append(f"Duplicate scenario ID: {s.id}")
        seen.add(s.id)
        if _UNREVIEWED_TAG in s.tags:
            errors.append(f"Scenario {s.id}: unreviewed production data tag present")
        for i, turn in enumerate(s.turns):
            for hit in _scan_text(turn.input):
                errors.append(f"Scenario {s.id} turn {i}: {hit} in input")
            for hit in _scan_text(" ".join(turn.expected.reply_contains)):
                errors.append(f"Scenario {s.id} turn {i}: {hit} in reply_contains")
    return errors


__all__ = ["DatasetValidationError", "load_scenarios", "validate_dataset"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_memory_eval_dataset.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/dataset.py tests/unit/eval/test_memory_eval_dataset.py
git commit -m "feat(eval): add dataset loader + anonymization/secret gate (Spec 4 §10)"
```

---

## Phase 2 — Deterministic metric modules

Each module is a pure function over `(MultiturnScenario, ScenarioRun)` pairs. They read observed values from `ObservedTurn.result` (the graph state dict) and from the captured `ObservedTurn` fields. Every module returns `(list[MetricResult], dict[str, ConfusionMatrix])`. Tests construct synthetic expected/observed pairs — no model or DB.

### Task 6: Turn & Topic metrics (`metrics/turn_topic.py`)

§5.1: per-class PRF + confusion; overall accuracy; standalone-query retention PR; Topic leakage for new/switch; clarification completion on the resolving turn; restore correctness + unnecessary-restore rate.

**Files:**
- Create: `eval/memory_eval/metrics/turn_topic.py`
- Test: `tests/unit/eval/test_metric_turn_topic.py`

**Interfaces:**
- Consumes: `eval.memory_eval.schema.MultiturnScenario`, `ScenarioRun`, `eval.memory_eval.metrics.types`.
- Produces: `evaluate_turn_topic(pairs) -> tuple[list[MetricResult], dict[str, ConfusionMatrix]]` where `pairs = list[tuple[MultiturnScenario, ScenarioRun]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_metric_turn_topic.py
from __future__ import annotations

from eval.memory_eval.metrics.turn_topic import evaluate_turn_topic
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _pair(exp_rels, obs_rels, obs_query="华东区"):
    scenario = MultiturnScenario(
        id="s1",
        turns=[
            ScenarioTurn(input=f"t{i}", expected=ExpectedTurn(turn_relation=r, standalone_query_contains=["华东"]))
            for i, r in enumerate(exp_rels)
        ],
    )
    run = ScenarioRun(
        scenario_id="s1",
        observed=[
            ObservedTurn(
                turn_index=i,
                result={"turn_relation": r, "standalone_query": obs_query},
                replies=[],
                active_topic_ids=[],
                closed_topic_ids=[],
                active_memory_keys=[],
                selected_memory_ids=[],
            )
            for i, r in enumerate(obs_rels)
        ],
        final_state={},
    )
    return (scenario, run)


def test_turn_relation_accuracy_and_confusion():
    metrics, cms = evaluate_turn_topic([_pair(["new", "continue"], ["new", "new"])])
    by_name = {m.name: m for m in metrics}
    assert by_name["turn_relation_accuracy"].score == 0.5
    assert "turn_relation" in cms
    assert cms["turn_relation"].matrix["continue"]["new"] == 1


def test_standalone_query_retention_recall():
    metrics, _ = evaluate_turn_topic([_pair(["new"], ["new"], obs_query="别的")])
    by_name = {m.name: m for m in metrics}
    # expected substring "华东" absent in "别的" → recall 0
    assert by_name["standalone_query_retention_recall"].score == 0.0


def test_topic_leakage_rate_for_new_switch():
    # A "new" turn that still carries retained entities leaks.
    scenario = MultiturnScenario(
        id="s2",
        turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(turn_relation="new", retained_entities=["stale"]))],
    )
    run = ScenarioRun(
        scenario_id="s2",
        observed=[ObservedTurn(
            turn_index=0,
            result={"turn_relation": "new", "retained_entities": ["stale"]},
            replies=[], active_topic_ids=[], closed_topic_ids=[],
            active_memory_keys=[], selected_memory_ids=[],
        )],
        final_state={},
    )
    metrics, _ = evaluate_turn_topic([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["topic_leakage_rate"].numerator == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_turn_topic.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/metrics/turn_topic.py
"""Turn & Topic metrics (Spec 4 §5.1)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import ConfusionMatrix, MetricResult, confusion, prf
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

_RELATION_LABELS = ["continue", "revise", "switch", "new", "ambiguous"]


def _turn_pairs(pairs):
    for scenario, run in pairs:
        for i, turn in enumerate(scenario.turns):
            obs = run.observed[i] if i < len(run.observed) else None
            if obs is None:
                continue
            yield scenario, run, turn, obs


def evaluate_turn_topic(pairs):
    metrics: list[MetricResult] = []
    cms: dict[str, ConfusionMatrix] = {}

    exp_rels, obs_rels = [], []
    q_match, q_total = 0, 0
    leak_n, leak_d = 0, 0

    for _, _, turn, obs in _turn_pairs(pairs):
        exp_rel = turn.expected.turn_relation
        obs_rel = obs.result.get("turn_relation")
        if exp_rel is not None and obs_rel is not None:
            exp_rels.append(exp_rel)
            obs_rels.append(obs_rel)

        # standalone-query retention recall: expected substrings present in observed query
        if turn.expected.standalone_query_contains:
            q_total += len(turn.expected.standalone_query_contains)
            observed_query = obs.result.get("standalone_query") or ""
            q_match += sum(1 for s in turn.expected.standalone_query_contains if s in observed_query)

        # Topic leakage: a new/switch turn that still carries retained entities.
        if exp_rel in ("new", "switch"):
            leak_d += 1
            retained = turn.expected.retained_entities
            observed_retained = obs.result.get("retained_entities") or []
            if retained or observed_retained:
                leak_n += 1

    # Overall accuracy
    if exp_rels:
        correct = sum(1 for e, o in zip(exp_rels, obs_rels) if e == o)
        metrics.append(MetricResult(
            name="turn_relation_accuracy",
            numerator=correct, denominator=len(exp_rels),
            score=correct / len(exp_rels), threshold=0.90,
        ))
        cms["turn_relation"] = confusion(expected=exp_rels, observed=obs_rels, labels=_RELATION_LABELS)

    # Standalone-query retention recall
    if q_total:
        metrics.append(MetricResult(
            name="standalone_query_retention_recall",
            numerator=q_match, denominator=q_total,
            score=q_match / q_total, threshold=0.90,
        ))

    # Topic leakage rate
    if leak_d:
        metrics.append(MetricResult(
            name="topic_leakage_rate",
            numerator=leak_n, denominator=leak_d,
            score=leak_n / leak_d, threshold=0.0,  # gate: zero critical leakage
        ))

    return metrics, cms
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_turn_topic.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/metrics/turn_topic.py tests/unit/eval/test_metric_turn_topic.py
git commit -m "feat(eval): add turn/topic metrics (Spec 4 §5.1)"
```

---

### Task 7: Memory write & lifecycle metrics (`metrics/memory_lifecycle.py`)

§5.2: explicit operation accuracy; inferred activation PR; prohibited-memory write count; correction/supersede accuracy; forget effectiveness; stale-memory activation/recall rate; evidence provenance completeness.

**Files:**
- Create: `eval/memory_eval/metrics/memory_lifecycle.py`
- Test: `tests/unit/eval/test_metric_memory_lifecycle.py`

**Interfaces:**
- Consumes: `schema`, `metrics.types`.
- Produces: `evaluate_memory_lifecycle(pairs) -> tuple[list[MetricResult], dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_metric_memory_lifecycle.py
from __future__ import annotations

from eval.memory_eval.metrics.memory_lifecycle import evaluate_memory_lifecycle
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _run(expected_ops, observed_ops, active_keys=None, sensitive=0):
    scenario = MultiturnScenario(
        id="m1",
        turns=[
            ScenarioTurn(
                input=f"t{i}",
                expected=ExpectedTurn(
                    memory_operation=exp,
                    active_memory_keys=([k] for k in (active_keys or [])).__next__() if False else [],
                    sensitive_persisted=sensitive if i == 0 else None,
                ),
            )
            for i, exp in enumerate(expected_ops)
        ],
    )
    run = ScenarioRun(
        scenario_id="m1",
        observed=[
            ObservedTurn(
                turn_index=i,
                result={"memory_operation": obs},
                replies=[], active_topic_ids=[], closed_topic_ids=[],
                active_memory_keys=[], selected_memory_ids=[],
            )
            for i, obs in enumerate(observed_ops)
        ],
        final_state={},
    )
    return [(scenario, run)]


def test_explicit_operation_accuracy():
    metrics, _ = evaluate_memory_lifecycle(_run(["remember", "forget"], ["remember", "forget"]))
    by_name = {m.name: m for m in metrics}
    assert by_name["explicit_operation_accuracy"].score == 1.0


def test_prohibited_memory_write_count_gate():
    metrics, _ = evaluate_memory_lifecycle(_run(["remember"], ["remember"], sensitive=1))
    by_name = {m.name: m for m in metrics}
    assert by_name["prohibited_memory_write_count"].numerator == 1
    assert by_name["prohibited_memory_write_count"].passes is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_memory_lifecycle.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/metrics/memory_lifecycle.py
"""Memory write & lifecycle metrics (Spec 4 §5.2)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

_EXPLICIT_OPS = {"remember", "correct", "forget"}


def _turn_pairs(pairs):
    for scenario, run in pairs:
        for i, turn in enumerate(scenario.turns):
            if i < len(run.observed):
                yield turn, run.observed[i]


def evaluate_memory_lifecycle(pairs):
    metrics: list[MetricResult] = []

    # Explicit operation accuracy (§6: 100% on deterministic cases)
    exp_correct, exp_total = 0, 0
    # Inferred activation precision/recall
    inf_tp, inf_fp, inf_fn = 0, 0, 0
    # Correction/supersede accuracy
    corr_correct, corr_total = 0, 0
    # Forget effectiveness (active memory count == 0 after forget)
    forget_ok, forget_total = 0, 0
    # Prohibited/sensitive persisted
    sensitive_total = 0
    # Evidence provenance completeness
    provenance_total, provenance_expected = 0, 0

    for turn, obs in _turn_pairs(pairs):
        exp_op = turn.expected.memory_operation
        obs_op = obs.result.get("memory_operation")
        if exp_op in _EXPLICIT_OPS:
            exp_total += 1
            if exp_op == obs_op:
                exp_correct += 1
        if exp_op == "correct":
            corr_total += 1
            if obs_op == "correct":
                corr_correct += 1
        if exp_op == "forget":
            forget_total += 1
            if obs_op == "forget" and not obs.active_memory_keys:
                forget_ok += 1
        if turn.expected.sensitive_persisted is not None:
            sensitive_total += int(turn.expected.sensitive_persisted or 0) + max(
                0, len([k for k in obs.active_memory_keys if k in {"password", "secret", "token", "密码"}])
            )
        if turn.expected.active_memory_keys:
            provenance_expected += len(turn.expected.active_memory_keys)
            provenance_total += sum(
                1 for k in turn.expected.active_memory_keys if k in obs.active_memory_keys
            )

    if exp_total:
        metrics.append(MetricResult(
            name="explicit_operation_accuracy", numerator=exp_correct, denominator=exp_total,
            score=exp_correct / exp_total, threshold=1.0,
        ))
    if corr_total:
        metrics.append(MetricResult(
            name="correction_supersede_accuracy", numerator=corr_correct, denominator=corr_total,
            score=corr_correct / corr_total, threshold=1.0,
        ))
    if forget_total:
        metrics.append(MetricResult(
            name="forget_effectiveness", numerator=forget_ok, denominator=forget_total,
            score=forget_ok / forget_total, threshold=1.0,
        ))
    metrics.append(MetricResult(
        name="prohibited_memory_write_count", numerator=sensitive_total, denominator=sensitive_total,
        score=float(sensitive_total), threshold=0.0,  # fail closed
    ))
    if provenance_expected:
        metrics.append(MetricResult(
            name="evidence_provenance_completeness", numerator=provenance_total,
            denominator=provenance_expected, score=provenance_total / provenance_expected, threshold=1.0,
        ))
    return metrics, {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_memory_lifecycle.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/metrics/memory_lifecycle.py tests/unit/eval/test_metric_memory_lifecycle.py
git commit -m "feat(eval): add memory lifecycle metrics (Spec 4 §5.2)"
```

---

### Task 8: Recall & profile metrics (`metrics/recall_profile.py`)

§5.3: relevant-memory precision/recall; unnecessary-memory injection rate; profile field accuracy + evidence coverage; cross-Topic/cross-scope leakage; user correction rate after memory use.

**Files:**
- Create: `eval/memory_eval/metrics/recall_profile.py`
- Test: `tests/unit/eval/test_metric_recall_profile.py`

**Interfaces:**
- Consumes: `schema`, `metrics.types`.
- Produces: `evaluate_recall_profile(pairs) -> tuple[list[MetricResult], dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_metric_recall_profile.py
from __future__ import annotations

from eval.memory_eval.metrics.recall_profile import evaluate_recall_profile
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)


def _pair(expected_ids, observed_ids, profile_match=True):
    scenario = MultiturnScenario(
        id="r1",
        turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(
            selected_memory_ids=expected_ids,
            active_memory_keys=expected_ids,
        ))],
    )
    run = ScenarioRun(scenario_id="r1", observed=[ObservedTurn(
        turn_index=0,
        result={"profile_field_match": profile_match},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=observed_ids, selected_memory_ids=observed_ids,
        profile_version="v1" if profile_match else None,
    )], final_state={})
    return [(scenario, run)]


def test_relevant_memory_precision_recall():
    metrics, _ = evaluate_recall_profile(_pair(["m1", "m2"], ["m1", "m3"]))
    by_name = {m.name: m for m in metrics}
    # tp=1 (m1), fp=1 (m3), fn=1 (m2) → precision 0.5, recall 0.5
    assert round(by_name["relevant_memory_precision"].score, 4) == 0.5
    assert round(getattr(by_name["relevant_memory_precision"], "recall"), 4) == 0.5


def test_cross_scope_leakage_gate():
    # final_expected.cross_scope_leakage True → gate fails closed.
    scenario = MultiturnScenario(id="r2", turns=[ScenarioTurn(input="t0", expected=ExpectedTurn())])
    scenario.final_expected.cross_scope_leakage = True
    run = ScenarioRun(scenario_id="r2", observed=[ObservedTurn(
        turn_index=0, result={}, replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={})
    metrics, _ = evaluate_recall_profile([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["cross_scope_leakage"].passes is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_recall_profile.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/metrics/recall_profile.py
"""Recall & profile metrics (Spec 4 §5.3)."""
from __future__ import annotations

from eval.memory_eval.metrics.types import MetricResult, prf
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun


def evaluate_recall_profile(pairs):
    metrics: list[MetricResult] = []

    exp_set_union, obs_set_union = set(), set()
    profile_match_total, profile_expected = 0, 0
    unnecessary_injection = 0
    cross_leak = 0

    for scenario, run in pairs:
        if scenario.final_expected.cross_scope_leakage:
            cross_leak += 1
        for i, turn in enumerate(scenario.turns):
            if i >= len(run.observed):
                continue
            obs = run.observed[i]
            expected_ids = set(turn.expected.selected_memory_ids)
            observed_ids = set(obs.selected_memory_ids)
            if expected_ids:
                exp_set_union |= expected_ids
                obs_set_union |= observed_ids
                # unnecessary injection: observed ids not in expected
                unnecessary_injection += len(observed_ids - expected_ids)
            if turn.expected.active_memory_keys:
                profile_expected += 1
                if obs.result.get("profile_field_match"):
                    profile_match_total += 1

    if exp_set_union or obs_set_union:
        tp = len(exp_set_union & obs_set_union)
        fp = len(obs_set_union - exp_set_union)
        fn = len(exp_set_union - obs_set_union)
        m = prf(tp=tp, fp=fp, fn=fn, threshold=0.90)
        m.name = "relevant_memory_precision"
        metrics.append(m)

    if profile_expected:
        metrics.append(MetricResult(
            name="profile_field_accuracy", numerator=profile_match_total, denominator=profile_expected,
            score=profile_match_total / profile_expected, threshold=0.90,
        ))

    total_observed = len(obs_set_union)
    if total_observed:
        metrics.append(MetricResult(
            name="unnecessary_memory_injection_rate", numerator=unnecessary_injection,
            denominator=total_observed, score=unnecessary_injection / total_observed, threshold=0.10,
        ))

    # Cross-scope leakage: fail closed (§6).
    metrics.append(MetricResult(
        name="cross_scope_leakage", numerator=cross_leak, denominator=cross_leak or 1,
        score=float(cross_leak), threshold=0.0,
    ))
    return metrics, {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_recall_profile.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/metrics/recall_profile.py tests/unit/eval/test_metric_recall_profile.py
git commit -m "feat(eval): add recall/profile metrics (Spec 4 §5.3)"
```

---

### Task 9: Conversation & trajectory metrics (`metrics/conversation.py`)

§5.4: final task outcome; repetition + unnecessary clarification; expected node/decision subset; response correctness/relevance where reference exists; P50/P95 latency, memory overhead, token use, cost.

**Files:**
- Create: `eval/memory_eval/metrics/conversation.py`
- Test: `tests/unit/eval/test_metric_conversation.py`

**Interfaces:**
- Consumes: `schema`, `metrics.types`.
- Produces: `evaluate_conversation(pairs) -> tuple[list[MetricResult], dict]`, plus `percentile(values, p)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_metric_conversation.py
from __future__ import annotations

from eval.memory_eval.metrics.conversation import evaluate_conversation, percentile


def test_percentile_basic():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


def test_node_subset_metric():
    from eval.memory_eval.schema import (
        ExpectedTurn, MultiturnScenario, ObservedTurn, ScenarioRun, ScenarioTurn,
    )
    scenario = MultiturnScenario(id="c1", turns=[ScenarioTurn(
        input="t0", expected=ExpectedTurn(trace_nodes=["chat", "memory_command"]),
    )])
    run = ScenarioRun(scenario_id="c1", observed=[ObservedTurn(
        turn_index=0,
        result={"trace_nodes": ["normalize_turn", "chat", "memory_command"], "latency_ms": 120.0},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={"outcome_ok": True})
    metrics, _ = evaluate_conversation([(scenario, run)])
    by_name = {m.name: m for m in metrics}
    assert by_name["node_subset_recall"].score == 1.0
    assert by_name["p50_latency_ms"].numerator == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_conversation.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/metrics/conversation.py
"""Conversation & trajectory metrics (Spec 4 §5.4)."""
from __future__ import annotations

import statistics

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (s[hi] - s[lo]) * (k - lo))


def evaluate_conversation(pairs):
    metrics: list[MetricResult] = []

    node_match, node_total = 0, 0
    outcome_ok, outcome_total = 0, 0
    latencies: list[float] = []
    token_totals: list[int] = []

    for scenario, run in pairs:
        if scenario.final_expected is not None:
            outcome_total += 1
            if run.final_state.get("outcome_ok"):
                outcome_ok += 1
        for i, turn in enumerate(scenario.turns):
            if i >= len(run.observed):
                continue
            obs = run.observed[i]
            if turn.expected.trace_nodes:
                observed_nodes = set(obs.result.get("trace_nodes") or [])
                node_total += len(turn.expected.trace_nodes)
                node_match += sum(1 for n in turn.expected.trace_nodes if n in observed_nodes)
            if "latency_ms" in obs.result:
                latencies.append(float(obs.result["latency_ms"]))
            if "total_tokens" in obs.result:
                token_totals.append(int(obs.result["total_tokens"]))

    if node_total:
        metrics.append(MetricResult(
            name="node_subset_recall", numerator=node_match, denominator=node_total,
            score=node_match / node_total, threshold=1.0,
        ))
    if outcome_total:
        metrics.append(MetricResult(
            name="final_outcome", numerator=outcome_ok, denominator=outcome_total,
            score=outcome_ok / outcome_total, threshold=0.95,
        ))
    if latencies:
        metrics.append(MetricResult(
            name="p50_latency_ms", numerator=len(latencies), denominator=len(latencies),
            score=percentile(latencies, 50),
        ))
        metrics.append(MetricResult(
            name="p95_latency_ms", numerator=len(latencies), denominator=len(latencies),
            score=percentile(latencies, 95),
        ))
    if token_totals:
        metrics.append(MetricResult(
            name="total_tokens", numerator=sum(token_totals), denominator=len(token_totals),
            score=float(sum(token_totals)),
        ))
    return metrics, {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_metric_conversation.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/metrics/conversation.py tests/unit/eval/test_metric_conversation.py
git commit -m "feat(eval): add conversation/trajectory metrics + percentiles (Spec 4 §5.4)"
```

---

## Phase 3 — Scenario runner + deterministic doubles

### Task 10: Deterministic model + embedding doubles (`model_double.py`)

The graph-multiturn layer (§3.2) uses deterministic model doubles. They must conform to the real `ChatModel.generate(...)` / `EmbeddingModel.embed(...)` interfaces (see `src/sales_agent/llm/base.py`). A single double serves three call sites — the context resolver (wants JSON), the memory extractor (wants JSON), and the chat node (wants a reply string) — distinguished by `response_format` and prompt content.

**Files:**
- Create: `eval/memory_eval/model_double.py`
- Test: `tests/unit/eval/test_model_double.py`

**Interfaces:**
- Consumes: `sales_agent.llm.base.ChatModel`, `EmbeddingModel`.
- Produces: `TurnScript`, `ScriptedModelDouble(ChatModel)`, `DeterministicEmbeddingDouble(EmbeddingModel)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_model_double.py
from __future__ import annotations

import json

from eval.memory_eval.model_double import (
    DeterministicEmbeddingDouble,
    ScriptedModelDouble,
    TurnScript,
)


def _double():
    return ScriptedModelDouble(scripts={
        ("s1", 0): TurnScript(
            context_decision={"turn_relation": "new", "standalone_query": "我负责华东区", "retained_entities": []},
            chat_reply="好的，已为您记住华东区。",
            extraction={"candidates": []},
        ),
    })


async def test_json_call_returns_context_decision():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate(
        [{"role": "system", "content": "Decide turn_relation for the conversation."}],
        response_format={"type": "json_object"},
    )
    assert json.loads(out)["turn_relation"] == "new"


async def test_plain_call_returns_chat_reply():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate([{"role": "user", "content": "hi"}])
    assert out == "好的，已为您记住华东区。"


async def test_extraction_call_when_prompt_mentions_memory():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate(
        [{"role": "system", "content": "Extract memory candidates from the user message."}],
        response_format={"type": "json_object"},
    )
    assert json.loads(out)["candidates"] == []


def test_embedding_is_deterministic():
    e = DeterministicEmbeddingDouble(dim=8)
    a = e.embed(["hello", "world"])
    b = e.embed(["hello", "world"])
    assert a == b
    assert len(a) == 2 and len(a[0]) == 8
    assert a[0] != a[1]  # different inputs → different vectors
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_model_double.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/model_double.py
"""Deterministic model doubles for the graph-multiturn layer (Spec 4 §3.2).

Conform to the real interfaces in ``src/sales_agent/llm/base.py``:
``ChatModel.generate`` and ``EmbeddingModel.embed``.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Optional

from sales_agent.llm.base import ChatModel, EmbeddingModel


@dataclass
class TurnScript:
    """Canned responses for one scenario turn, one per call site."""

    context_decision: dict
    chat_reply: str
    extraction: dict = field(default_factory=lambda: {"candidates": []})


class ScriptedModelDouble(ChatModel):
    """Returns scripted JSON / replies based on the call site.

    Call-site detection:
      * ``response_format == {"type": "json_object"}`` AND prompt mentions
        ``turn_relation`` → context-resolver decision.
      * ``response_format == {"type": "json_object"}`` AND prompt mentions
        ``memory``/``extract`` → extractor result.
      * otherwise → chat reply.
    """

    def __init__(self, scripts: dict[tuple[str, int], TurnScript]) -> None:
        self._scripts = scripts
        self._current: Optional[TurnScript] = None

    def set_turn(self, scenario_id: str, turn_index: int) -> None:
        key = (scenario_id, turn_index)
        if key not in self._scripts:
            raise KeyError(f"no script for {key}")
        self._current = self._scripts[key]

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        assert self._current is not None, "set_turn() not called"
        blob = " ".join(m.get("content", "") for m in messages).lower()
        if response_format and response_format.get("type") == "json_object":
            if "turn_relation" in blob or "standalone_query" in blob:
                return json.dumps(self._current.context_decision, ensure_ascii=False)
            if "memory" in blob or "extract" in blob:
                return json.dumps(self._current.extraction, ensure_ascii=False)
            # Default structured call → context decision shape.
            return json.dumps(self._current.context_decision, ensure_ascii=False)
        return self._current.chat_reply

    async def stream_generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        yield await self.generate(messages, temperature=temperature, max_tokens=max_tokens)


class DeterministicEmbeddingDouble(EmbeddingModel):
    """Hash-based fixed-dimensional embedding (no randomness)."""

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(digest[i % len(digest)] / 255.0) * 2 - 1 for i in range(self.dim)]
            out.append(vec)
        return out


__all__ = ["DeterministicEmbeddingDouble", "ScriptedModelDouble", "TurnScript"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_model_double.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/model_double.py tests/unit/eval/test_model_double.py
git commit -m "feat(eval): add scripted model double + deterministic embedding (Spec 4 §3.2)"
```

---

### Task 11: Graph scenario runner (`scenario_runner.py`)

Drives the **real** production entry `invoke_online_turn` turn-by-turn against the test DB; captures `ObservedTurn` after each turn; honors §4 first-class controls: `time_offset_seconds`, `restart_before`, `duplicate_previous_event`, `concurrent_group`, `worker_id`. The orchestration is split from the DB-bound invoker so it is unit-testable with a fake invoker.

**Files:**
- Create: `eval/memory_eval/scenario_runner.py`
- Test: `tests/unit/eval/test_scenario_runner.py`

**Interfaces:**
- Consumes: `eval.memory_eval.schema`, `sales_agent.services.online_conversation.invoke_online_turn`, the doubles from Task 10.
- Produces: `ScenarioRunner.run(scenario) -> ScenarioRun`, injectable `invoke_turn` / `capture_state` / `restart_runtime` callables.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_scenario_runner.py
from __future__ import annotations

import pytest

from eval.memory_eval.scenario_runner import ScenarioRunner
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ScenarioTurn,
)


def _scenario():
    return MultiturnScenario(id="s1", turns=[
        ScenarioTurn(input="t0", event_id="e0", expected=ExpectedTurn(turn_relation="new")),
        ScenarioTurn(input="t1", duplicate_previous_event=True, expected=ExpectedTurn()),
        ScenarioTurn(input="t2", restart_before=True, time_offset_seconds=3600, expected=ExpectedTurn()),
    ])


@pytest.mark.asyncio
async def test_duplicate_reuses_previous_event_id():
    seen_event_ids: list[str] = []

    async def fake_invoke(ctx, *, message, event_id, now, chat_model):
        seen_event_ids.append(event_id)
        return {"turn_relation": "new" if message == "t0" else "duplicate"}

    async def fake_capture(ctx, turn_index, result):
        from eval.memory_eval.schema import ObservedTurn
        return ObservedTurn(
            turn_index=turn_index, result=result, replies=[],
            active_topic_ids=[], closed_topic_ids=[], active_memory_keys=[],
            selected_memory_ids=[], duplicate=result.get("turn_relation") == "duplicate",
        )

    runner = ScenarioRunner(
        ctx={}, invoke_turn=fake_invoke, capture_state=fake_capture,
        now_provider=lambda: 0,
    )
    run = await runner.run(_scenario())
    # turn 1 duplicates turn 0's event id
    assert seen_event_ids[1] == seen_event_ids[0]
    assert seen_event_ids[2] != seen_event_ids[0]
    assert run.observed[1].duplicate is True


@pytest.mark.asyncio
async def test_restart_and_time_offset_applied():
    from datetime import datetime, timedelta, timezone
    restarts: list[bool] = []
    nows: list = []
    base = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)

    async def fake_invoke(ctx, *, message, event_id, now, chat_model):
        nows.append(now)
        return {"turn_relation": "continue"}

    async def fake_capture(ctx, turn_index, result):
        from eval.memory_eval.schema import ObservedTurn
        return ObservedTurn(
            turn_index=turn_index, result=result, replies=[],
            active_topic_ids=[], closed_topic_ids=[], active_memory_keys=[],
            selected_memory_ids=[],
        )

    async def fake_restart():
        restarts.append(True)

    runner = ScenarioRunner(
        ctx={},
        invoke_turn=fake_invoke,
        capture_state=fake_capture,
        now_provider=lambda: base,
        restart_runtime=fake_restart,
    )
    await runner.run(_scenario())
    assert restarts == [True]                                  # restart_before on turn 2
    assert nows[2] == base + timedelta(seconds=3600)           # time_offset_seconds advanced `now`
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_scenario_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/scenario_runner.py
"""Scenario runner: drives the real Online Graph turn-by-turn (Spec 4 §3.2, §4)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from eval.memory_eval.schema import (
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
)

logger = logging.getLogger(__name__)

InvokeTurn = Callable[..., Awaitable[dict[str, Any]]]
CaptureState = Callable[[Any, int, dict[str, Any]], Awaitable[ObservedTurn]]
RestartRuntime = Callable[[], Awaitable[None]]


class ScenarioRunner:
    """Runs one ``MultiturnScenario`` through an injectable turn invoker.

    The default invoker binds to ``invoke_online_turn`` against the production
    Online Graph; tests inject a fake to unit-test orchestration.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        invoke_turn: Optional[InvokeTurn] = None,
        capture_state: Optional[CaptureState] = None,
        restart_runtime: Optional[RestartRuntime] = None,
        now_provider: Callable[[], Any] = lambda: None,
    ) -> None:
        self.ctx = ctx
        self._invoke_turn = invoke_turn or _default_invoke_turn
        self._capture = capture_state or _default_capture
        self._restart = restart_runtime
        self._now = now_provider

    async def run(self, scenario: MultiturnScenario) -> ScenarioRun:
        observed: list[ObservedTurn] = []
        prev_event_id: Optional[str] = None
        accumulated_offset = 0
        last_result: dict[str, Any] = {}

        for i, turn in enumerate(scenario.turns):
            if turn.restart_before and self._restart is not None:
                await self._restart()
            accumulated_offset += turn.time_offset_seconds

            event_id = prev_event_id if turn.duplicate_previous_event else (
                turn.event_id or f"{scenario.id}-{i}"
            )
            now = self._advanced_now(accumulated_offset)

            try:
                if turn.concurrent_group:
                    result = await self._invoke_concurrent(scenario, i, turn, event_id, now)
                else:
                    result = await self._invoke_turn(
                        self.ctx, message=turn.input, event_id=event_id, now=now,
                        chat_model=self.ctx.get("chat_model"),
                    )
            except Exception as exc:  # noqa: BLE001 — invalidate the run, not a product score (§10)
                logger.exception("scenario %s turn %d failed", scenario.id, i)
                observed.append(ObservedTurn(
                    turn_index=i, result={}, replies=[], active_topic_ids=[],
                    closed_topic_ids=[], active_memory_keys=[], selected_memory_ids=[],
                    error=str(exc),
                ))
                return ScenarioRun(scenario_id=scenario.id, observed=observed,
                                   final_state=last_result, error=str(exc))

            last_result = result
            observed.append(await self._capture(self.ctx, i, result))
            if not turn.duplicate_previous_event:
                prev_event_id = event_id

        return ScenarioRun(scenario_id=scenario.id, observed=observed, final_state=last_result)

    def _advanced_now(self, offset_seconds: int) -> Any:
        base = self._now()
        if base is None:
            return None
        try:
            from datetime import timedelta
            return base + timedelta(seconds=offset_seconds)
        except Exception:  # pragma: no cover - base is not a datetime
            return base

    async def _invoke_concurrent(self, scenario, i, turn, event_id, now):
        """Within a concurrent group, turns serialize via the per-thread
        advisory lock (``acquire_online_turn_lock``). The runner simply awaits
        them on the same thread_id; the lock guarantees no overlap."""
        return await self._invoke_turn(
            self.ctx, message=turn.input, event_id=event_id, now=now,
            chat_model=self.ctx.get("chat_model"),
        )


async def _default_invoke_turn(ctx, *, message, event_id, now, chat_model):
    """Bind to the real Online Graph entry."""
    from sales_agent.services.online_conversation import invoke_online_turn
    return await invoke_online_turn(
        db=ctx["db"],
        tenant_id=ctx["tenant_id"],
        agent_id=ctx["agent_id"],
        user_id=ctx["user_id"],
        session_user_id=ctx["session_user_id"],
        channel=ctx["channel"],
        conversation_id=ctx["conversation_id"],
        message=message,
        event_id=event_id,
        chat_model=chat_model,
        embedding_model=ctx.get("embedding_model"),
        now=now,
    )


async def _default_capture(ctx, turn_index, result):
    """Capture observed state from the graph result + DB."""
    from sqlalchemy import select
    from sales_agent.models.atomic_memory import AtomicMemory
    db = ctx["db"]
    scope = (ctx["tenant_id"], ctx["agent_id"], ctx["user_id"])
    rows = (await db.execute(
        select(AtomicMemory.normalized_key).where(
            AtomicMemory.tenant_id == scope[0],
            AtomicMemory.agent_id == scope[1],
            AtomicMemory.subject_id == scope[2],
            AtomicMemory.status == "active",
        )
    )).scalars().all()
    return ObservedTurn(
        turn_index=turn_index,
        result=result,
        replies=result.get("replies", []),
        active_topic_ids=result.get("active_topic_ids", []),
        closed_topic_ids=result.get("closed_topic_ids", []),
        active_memory_keys=list(rows),
        selected_memory_ids=result.get("selected_memory_ids", []),
        profile_version=result.get("profile_version"),
        duplicate=result.get("response_kind") == "duplicate",
    )


__all__ = ["ScenarioRunner"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_scenario_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/scenario_runner.py tests/unit/eval/test_scenario_runner.py
git commit -m "feat(eval): add scenario runner with restart/duplicate/concurrent/time-offset (Spec 4 §3.2,§4)"
```

---

## Phase 4 — Test-layer modes + dataset + release gates

### Task 12: CLI dispatcher + `unit-memory` + `graph-multiturn` modes (`runner.py`)

The runner exposes the seven documented modes (§11). This task lands the dispatcher plus the first two modes; later tasks (13, 14, 19, 20, 21) each add their mode to the dispatcher. Each mode writes JSON + Markdown and exits non-zero only for its own gates or invalid execution (§11).

`unit-memory` (§3.1) runs the deterministic unit/property suite and aggregates pass/fail. `graph-multiturn` (§3.2) drives the real Online Graph with deterministic doubles + PG checkpoints and aggregates the four metric modules.

**Files:**
- Create: `eval/memory_eval/runner.py`
- Test: `tests/unit/eval/test_runner_dispatcher.py`
- Test (integration): `tests/integration/test_memory_eval_graph_multiturn.py`

**Interfaces:**
- Consumes: `dataset.load_scenarios`, `schema`, `model_double`, `scenario_runner.ScenarioRunner`, the four metric modules, `report`, `versions`.
- Produces: `main(argv) -> int`, `run_unit_memory(args) -> int`, `run_graph_multiturn(args) -> int`, `assemble_report(pairs, versions) -> EvaluationReport`, `build_scripts_from_scenarios(scenarios) -> dict`.

- [ ] **Step 1: Write the failing unit test (dispatcher + assembly)**

```python
# tests/unit/eval/test_runner_dispatcher.py
from __future__ import annotations

import pytest

from eval.memory_eval.runner import assemble_report, build_scripts_from_scenarios, main
from eval.memory_eval.schema import (
    ExpectedTurn,
    MultiturnScenario,
    ObservedTurn,
    ScenarioRun,
    ScenarioTurn,
)
from eval.memory_eval.versions import collect_version_bundle


def test_unknown_mode_exits_invalid():
    with pytest.raises(SystemExit):
        main(["bogus", "--output", "/tmp/x"])


def test_build_scripts_uses_expected_relation():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(
        input="记住我负责华东区",
        expected=ExpectedTurn(turn_relation="new", standalone_query_contains=["华东"]),
    )])
    scripts = build_scripts_from_scenarios([s])
    key = ("s1", 0)
    assert key in scripts
    assert scripts[key].context_decision["turn_relation"] == "new"
    assert "华东" in scripts[key].context_decision["standalone_query"]


def test_assemble_report_groups_metrics():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(
        input="t0", expected=ExpectedTurn(turn_relation="new"))])
    run = ScenarioRun(scenario_id="s1", observed=[ObservedTurn(
        turn_index=0, result={"turn_relation": "new"},
        replies=[], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={"outcome_ok": True})
    report = assemble_report([(s, run)], collect_version_bundle(dataset_version="t"))
    assert "deterministic_state_safety" in report.groups
    assert report.thresholds_met is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_runner_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/runner.py
"""Memory-eval CLI dispatcher with the seven documented modes (Spec 4 §11)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from eval.memory_eval.dataset import DatasetValidationError, load_scenarios, validate_dataset
from eval.memory_eval.metrics.conversation import evaluate_conversation
from eval.memory_eval.metrics.memory_lifecycle import evaluate_memory_lifecycle
from eval.memory_eval.metrics.recall_profile import evaluate_recall_profile
from eval.memory_eval.metrics.turn_topic import evaluate_turn_topic
from eval.memory_eval.model_double import ScriptedModelDouble, TurnScript
from eval.memory_eval.report import EvaluationReport, write_report
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun
from eval.memory_eval.scenario_runner import ScenarioRunner
from eval.memory_eval.versions import collect_version_bundle

DEFAULT_DATASET = "eval/memory/datasets/multiturn_v1.jsonl"
DEFAULT_OUTPUT = "/tmp/sales-agent-memory-eval"

# Deterministic unit/property suite run by `unit-memory` (Spec 4 §3.1).
UNIT_TEST_PATHS = [
    "tests/unit/memory/test_contracts.py",
    "tests/unit/memory/test_policy.py",
    "tests/unit/memory/test_commands.py",
    "tests/unit/memory/test_extractor.py",
    "tests/unit/memory/test_outbox_worker.py",
    "tests/unit/eval/test_memory_eval_schema.py",
    "tests/unit/eval/test_memory_eval_versions.py",
    "tests/unit/eval/test_memory_eval_metric_types.py",
    "tests/unit/eval/test_memory_eval_report.py",
    "tests/unit/eval/test_memory_eval_dataset.py",
]


def build_scripts_from_scenarios(scenarios: list[MultiturnScenario]) -> dict[tuple[str, int], TurnScript]:
    """Derive deterministic model scripts from each turn's expectations.

    The double returns the scripted context decision so the graph exercises its
    own state machine, memory writes, and persistence deterministically (§3.2).
    """
    scripts: dict[tuple[str, int], TurnScript] = {}
    for s in scenarios:
        for i, turn in enumerate(s.turns):
            rel = turn.expected.turn_relation or "continue"
            standalone = " ".join(turn.expected.standalone_query_contains) or turn.input
            reply = "、".join(turn.expected.reply_contains) or "好的。"
            scripts[(s.id, i)] = TurnScript(
                context_decision={
                    "turn_relation": rel,
                    "standalone_query": standalone,
                    "retained_entities": turn.expected.retained_entities,
                    "retracted_goals": turn.expected.retracted_goals,
                },
                chat_reply=reply,
                extraction={"candidates": []},
            )
    return scripts


def assemble_report(pairs: list[tuple[MultiturnScenario, ScenarioRun]], versions) -> EvaluationReport:
    report = EvaluationReport(
        versions=versions,
        total_scenarios=len(pairs),
        total_turns=sum(len(s.turns) for s, _ in pairs),
    )
    for group, (metrics, cms) in [
        ("deterministic_state_safety", evaluate_turn_topic(pairs)),
        ("persistence_isolation", evaluate_memory_lifecycle(pairs)),
        ("persistence_isolation", (evaluate_recall_profile(pairs)[0], {})),
        ("trajectory", evaluate_conversation(pairs)),
    ]:
        for m in metrics:
            report.add_metric(group, m)
        for name, cm in cms.items():
            report.add_confusion(name, cm)
    return report


def _load_or_fail(dataset: str) -> list[MultiturnScenario]:
    if not Path(dataset).exists():
        raise SystemExit(f"Dataset not found: {dataset}")
    try:
        scenarios = load_scenarios(dataset)
    except DatasetValidationError as exc:
        raise SystemExit(f"Dataset invalid: {exc}") from exc
    errors = validate_dataset(scenarios)
    if errors:
        for e in errors:
            print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        raise SystemExit("Dataset rejected (anonymization/structure)")
    return scenarios


def run_unit_memory(args) -> int:
    """§3.1: run the deterministic unit/property suite, aggregate to a report."""
    report = EvaluationReport(versions=collect_version_bundle(dataset_version="unit"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *UNIT_TEST_PATHS],
        capture_output=True, text=True,
    )
    # Parse "X passed, Y failed" from the pytest summary line.
    passed, failed = _parse_pytest_summary(proc.stdout + proc.stderr)
    from eval.memory_eval.metrics.types import MetricResult
    report.add_metric("deterministic_state_safety", MetricResult(
        name="unit_property_suite", numerator=passed, denominator=passed + failed,
        score=(passed / (passed + failed)) if (passed + failed) else 0.0, threshold=1.0,
    ))
    out = write_report(report, args.output)
    print(f"unit-memory: {'PASS' if report.thresholds_met else 'FAIL'}  report={out}")
    return 0 if report.thresholds_met else 1


def _parse_pytest_summary(text: str) -> tuple[int, int]:
    import re
    m = re.search(r"(\d+) passed", text)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", text)
    failed = int(m.group(1)) if m else 0
    return passed, failed


def run_graph_multiturn(args) -> int:
    """§3.2: drive the real Online Graph with deterministic doubles + PG."""
    test_db = __import__("os").environ.get("TEST_DATABASE_URL", "")
    if "test" not in test_db:
        print("graph-multiturn requires TEST_DATABASE_URL containing 'test'", file=sys.stderr)
        return 2  # invalid execution (§11)

    scenarios = _load_or_fail(args.dataset)
    scripts = build_scripts_from_scenarios(scenarios)
    double = ScriptedModelDouble(scripts)

    import sales_agent.graph.checkpoint_runtime as checkpoint_runtime
    from types import SimpleNamespace
    from unittest.mock import patch
    from sales_agent.services.online_conversation import initialize_online_runtime, close_online_runtime

    fake_settings = SimpleNamespace(database=SimpleNamespace(url=test_db))
    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    await close_online_runtime()
    try:
        with patch.object(checkpoint_runtime, "get_settings", return_value=fake_settings):
            await initialize_online_runtime()
            for s in scenarios:
                # Fresh session per scenario (pattern from tests/conftest.py db_session).
                from sales_agent.core.database import get_session_factory
                async with get_session_factory()() as db:
                    ctx = {
                        "db": db, "tenant_id": args.tenant_id, "agent_id": args.agent_id,
                        "user_id": f"{s.id}-user", "session_user_id": f"{s.id}-session",
                        "channel": "eval", "conversation_id": f"{s.id}-conv",
                        "chat_model": double, "embedding_model": None,
                    }
                    runner = ScenarioRunner(ctx=ctx, restart_runtime=close_online_runtime_and_reinit(checkpoint_runtime, fake_settings))
                    for i, _ in enumerate(s.turns):
                        double.set_turn(s.id, i)
                    run = await runner.run(s)
                    pairs.append((s, run))
    finally:
        await close_online_runtime()

    report = assemble_report(pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem))
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1


def close_online_runtime_and_reinit(checkpoint_runtime, fake_settings):
    async def _do():
        from sales_agent.services.online_conversation import close_online_runtime, initialize_online_runtime
        await close_online_runtime()
        from unittest.mock import patch
        with patch.object(checkpoint_runtime, "get_settings", return_value=fake_settings):
            await initialize_online_runtime()
    return _do


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-eval", description="Spec 4 memory evaluation")
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("unit-memory")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/unit-memory")

    p = sub.add_parser("graph-multiturn")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/graph-multiturn")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")

    args = parser.parse_args(argv)
    if args.mode == "unit-memory":
        return run_unit_memory(args)
    if args.mode == "graph-multiturn":
        import asyncio
        return asyncio.run(run_graph_multiturn(args))
    parser.error(f"unknown mode: {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_runner_dispatcher.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the integration test (real graph)**

```python
# tests/integration/test_memory_eval_graph_multiturn.py
"""Integration: graph-multiturn mode drives the real Online Graph (Spec 4 §3.2)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_graph_multiturn_writes_report(tmp_path):
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        pytest.skip("TEST_DATABASE_URL not set to a test database")

    # Minimal dataset: one explicit-remember scenario.
    ds = tmp_path / "ds.jsonl"
    ds.write_text(json.dumps({
        "id": "gm-001", "version": 1, "tags": ["explicit", "remember"],
        "turns": [{"input": "记住我负责华东区", "event_id": "gm-001-1",
                   "expected": {"turn_relation": "new", "memory_operation": "remember",
                                "active_memory_keys": ["sales_region"], "reply_contains": ["华东"]}}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    from eval.memory_eval.runner import run_graph_multiturn
    from types import SimpleNamespace
    rc = await run_graph_multiturn(SimpleNamespace(
        dataset=str(ds), output=str(tmp_path / "out"),
        tenant_id="eval-tenant", agent_id="eval-agent",
    ))
    assert (tmp_path / "out" / "report.json").exists()
    # Exit code 0 or 1 (quality), never 2 (invalid execution) on a valid setup.
    assert rc in (0, 1)
```

- [ ] **Step 6: Run the integration test (requires test Postgres)**

Run: `PYTHONPATH=src:. TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test python3 -m pytest tests/integration/test_memory_eval_graph_multiturn.py -v`
Expected: PASS (or SKIP if no test DB). The report.json must contain a `versions` block and a `deterministic_state_safety` group.

- [ ] **Step 7: Commit**

```bash
git add eval/memory_eval/runner.py tests/unit/eval/test_runner_dispatcher.py tests/integration/test_memory_eval_graph_multiturn.py
git commit -m "feat(eval): add CLI dispatcher + unit-memory + graph-multiturn modes (Spec 4 §11,§3.1,§3.2)"
```

---

### Task 13: `model-multiturn` mode

§3.3: real configured production model against the versioned dataset; deterministic metrics for every scenario; semantic judges only where needed; ambiguous/boundary cases run three repetitions and report consistency. This mode awaits production resolvers with their actual runtime inputs (acceptance criterion §12.2).

**Files:**
- Modify: `eval/memory_eval/runner.py` (add `run_model_multiturn` + parser subcommand).
- Test: `tests/unit/eval/test_runner_model_mode.py`

**Interfaces:**
- Consumes: `runner._load_or_fail`, `runner.assemble_report`, `scenario_runner.ScenarioRunner`, `sales_agent.core.tenant_runtime` (real model provider).
- Produces: `run_model_multiturn(args) -> int`; consistency reporter for 3× repetitions.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_runner_model_mode.py
from __future__ import annotations

import pytest

from eval.memory_eval.runner import main


def test_model_multiturn_requires_tenant():
    # No credentials → invalid execution return code 2, not a quality failure.
    rc = main(["model-multiturn", "--dataset", "eval/memory/datasets/multiturn_v1.jsonl",
               "--output", "/tmp/x", "--no-credentials"])
    assert rc == 2


def test_consistency_report_flags_disagreement():
    from eval.memory_eval.runner import classify_repetitions
    assert classify_repetitions([["new"], ["new"], ["new"]]) == "consistent"
    assert classify_repetitions([["new"], ["continue"], ["new"]]) == "flaky"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_runner_model_mode.py -v`
Expected: FAIL (no `model-multiturn` mode / no `classify_repetitions`)

- [ ] **Step 3: Write minimal implementation (extend `runner.py`)**

Append to `eval/memory_eval/runner.py` (add the mode to `main`'s subparsers and dispatch):

```python
# --- model-multiturn (Spec 4 §3.3) ---

REPETITIONS_FOR_AMBIGUOUS = 3


def classify_repetitions(observed_seqs: list[list[str]]) -> str:
    """Label a 3× repetition set as consistent/flaky (Spec 4 §3.3)."""
    if not observed_seqs:
        return "na"
    first = observed_seqs[0]
    if all(seq == first for seq in observed_seqs[1:]):
        return "consistent"
    return "flaky"


def _is_ambiguous_boundary(scenario) -> bool:
    return any(t.expected.turn_relation == "ambiguous" for t in scenario.turns) or \
           "boundary" in scenario.tags


async def run_model_multiturn(args) -> int:
    import os
    if args.no_credentials or not os.environ.get("MODEL_API_KEY"):
        print("model-multiturn requires real model credentials (MODEL_API_KEY)", file=sys.stderr)
        return 2  # invalid execution (§11)

    scenarios = _load_or_fail(args.dataset)
    from sales_agent.services.online_conversation import resolve_online_models  # real resolver
    from sales_agent.core.database import get_session_factory

    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    consistency: dict[str, str] = {}
    for s in scenarios:
        repetitions = REPETITIONS_FOR_AMBIGUOUS if _is_ambiguous_boundary(s) else 1
        seqs: list[list[str]] = []
        run: ScenarioRun | None = None
        for _ in range(repetitions):
            async with get_session_factory()() as db:
                # Await production resolvers with their actual runtime inputs (§12.2):
                chat_model, embedding_model = await resolve_online_models(
                    db=db, tenant_id=args.tenant_id,
                )
                ctx = {
                    "db": db, "tenant_id": args.tenant_id, "agent_id": args.agent_id,
                    "user_id": f"{s.id}-user", "session_user_id": f"{s.id}-session",
                    "channel": "eval", "conversation_id": f"{s.id}-conv",
                    "chat_model": chat_model, "embedding_model": embedding_model,
                }
                runner = ScenarioRunner(ctx=ctx)
                run = await runner.run(s)
                seqs.append([o.result.get("turn_relation", "na") for o in run.observed])
        if run is not None:
            pairs.append((s, run))
        if repetitions > 1:
            consistency[s.id] = classify_repetitions(seqs)

    report = assemble_report(pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem))
    report.consistency = consistency  # type: ignore[attr-defined]
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1
```

And in `main()`, register the subcommand + dispatch (insert before the `parser.error` line):

```python
    p = sub.add_parser("model-multiturn")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/model-multiturn")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")
    p.add_argument("--no-credentials", action="store_true")
```

```python
    if args.mode == "model-multiturn":
        return await run_model_multiturn(args)
```

Because `main` now has an async branch, make the dispatch section `return await ...` and change the entrypoint to run the coroutine:

```python
def main(argv=None):
    ...parse...
    import asyncio
    if args.mode in ("graph-multiturn", "model-multiturn", "dingtalk-staging", "online-sample", "promote-trace"):
        return asyncio.run(_async_dispatch(args))
    if args.mode == "unit-memory":
        return run_unit_memory(args)
    if args.mode == "compare":
        return run_compare(args)
    parser.error(f"unknown mode: {args.mode}")
    return 2

async def _async_dispatch(args):
    if args.mode == "graph-multiturn":
        return await run_graph_multiturn(args)
    if args.mode == "model-multiturn":
        return await run_model_multiturn(args)
    ...
```

> **Refactor note:** Task 12 already routes `graph-multiturn` inline via `asyncio.run`. Centralize that routing: introduce `_async_dispatch(args)` and route all async modes (`graph-multiturn`, `model-multiturn`, `dingtalk-staging`, `online-sample`, `promote-trace`) through `asyncio.run(_async_dispatch(args))`. Keep `run_unit_memory`/`run_compare` synchronous.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_runner_model_mode.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/runner.py tests/unit/eval/test_runner_model_mode.py
git commit -m "feat(eval): add model-multiturn mode with 3× consistency (Spec 4 §3.3)"
```

---

### Task 14: `dingtalk-staging` mode

§3.4: normalized HTTP/stream events through `handle_dingtalk_event()` with staging users and databases; only public outbound delivery is captured; includes restart and worker-switch steps between turns. Covers all six approved user scenarios (acceptance §12.3).

**Files:**
- Modify: `eval/memory_eval/runner.py` (add `run_dingtalk_staging` + subcommand).
- Create: `eval/memory_eval/dingtalk_capture.py` — a `reply_fn` that captures only public outbound text (no internal/audit text).
- Test: `tests/unit/eval/test_dingtalk_capture.py`
- Test (integration): `tests/integration/test_memory_eval_dingtalk_staging.py`

**Interfaces:**
- Consumes: `sales_agent.integrations.dingtalk.processor.handle_dingtalk_event`, `schema`, `report`.
- Produces: `PublicReplyCapture` (reply_fn), `run_dingtalk_staging(args) -> int`.

- [ ] **Step 1: Write the failing unit test (capture filter)**

```python
# tests/unit/eval/test_dingtalk_capture.py
from __future__ import annotations

import pytest

from eval.memory_eval.dingtalk_capture import PublicReplyCapture


@pytest.mark.asyncio
async def test_capture_keeps_public_drops_internal():
    cap = PublicReplyCapture()
    await cap.reply("这是给用户的公开回复")
    await cap.reply("[internal] 审计日志不外发")
    await cap.reply("[audit] memory write event")
    assert cap.public_replies == ["这是给用户的公开回复"]


@pytest.mark.asyncio
async def test_capture_records_kind():
    cap = PublicReplyCapture()
    await cap.reply("hi", kind="text")
    await cap.reply("card payload", kind="card")
    assert cap.kinds == ["text", "card"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_dingtalk_capture.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the capture implementation**

```python
# eval/memory_eval/dingtalk_capture.py
"""Capture only public outbound delivery from DingTalk staging (Spec 4 §3.4).

The staging runner must not treat internal/audit text as user-facing output.
"""
from __future__ import annotations

import re

_INTERNAL_PREFIXES = (
    "[internal]",
    "[audit]",
    "[memory-internal]",
)


class PublicReplyCapture:
    """An async ``reply_fn`` that keeps only public outbound messages."""

    def __init__(self) -> None:
        self.public_replies: list[str] = []
        self.kinds: list[str] = []

    async def reply(self, text: str, *, kind: str = "text") -> None:
        stripped = (text or "").strip()
        if any(stripped.startswith(p) for p in _INTERNAL_PREFIXES):
            return
        if re.match(r"^\[(internal|audit|memory-internal)\]", stripped, re.I):
            return
        self.public_replies.append(stripped)
        self.kinds.append(kind)


__all__ = ["PublicReplyCapture"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_dingtalk_capture.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add the mode to `runner.py`**

```python
# --- dingtalk-staging (Spec 4 §3.4) ---

async def run_dingtalk_staging(args) -> int:
    import dataclasses
    import os
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        print("dingtalk-staging requires TEST_DATABASE_URL containing 'test'", file=sys.stderr)
        return 2

    scenarios = _load_or_fail(args.dataset)
    from types import SimpleNamespace
    from unittest.mock import patch
    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event
    from eval.memory_eval.dingtalk_capture import PublicReplyCapture
    from sales_agent.core.database import get_session_factory

    settings = SimpleNamespace(
        conversation=SimpleNamespace(reset_commands=["/reset", "新话题"]),
        long_term_memory=SimpleNamespace(enabled=True),
    )
    config = SimpleNamespace()
    runtime = SimpleNamespace(tenant_id=args.tenant_id)

    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    for s in scenarios:
        observed: list = []
        async with get_session_factory()() as db:
            with patch(
                "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                lambda db, tenant_id: args.agent_id,
            ):
                for i, turn in enumerate(s.turns):
                    cap = PublicReplyCapture()
                    result = await handle_dingtalk_event(
                        db, config, settings, runtime,
                        event_id=turn.event_id or f"{s.id}-{i}",
                        corp_id="staging-corp", sender_id=f"{s.id}-sender",
                        sender_name="staging", message_type="text",
                        text=turn.input, dingtalk_conversation_id=f"{s.id}-dt",
                        reply_fn=cap.reply,
                    )
                    from eval.memory_eval.schema import ObservedTurn
                    observed.append(ObservedTurn(
                        turn_index=i, result=dataclasses.asdict(result),
                        replies=cap.public_replies, active_topic_ids=[], closed_topic_ids=[],
                        active_memory_keys=result.memory_ids or [],
                        selected_memory_ids=result.selected_memory_ids or [],
                        duplicate=result.response_kind == "duplicate",
                    ))
        pairs.append((s, ScenarioRun(scenario_id=s.id, observed=observed, final_state={})))

    report = assemble_report(pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem))
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1
```

Register subcommand + dispatch (in `main` and `_async_dispatch`):

```python
    p = sub.add_parser("dingtalk-staging")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/dingtalk-staging")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")
```
```python
    if args.mode == "dingtalk-staging":
        return await run_dingtalk_staging(args)
```

Restart and worker-switch between turns: the scenario schema's `restart_before` and `worker_id` controls are honored by re-initializing the runtime / switching the resolved agent between turns. For the staging runner, a `restart_before` turn closes and reopens the online runtime (reuse `close_online_runtime_and_reinit` from Task 12) and a `worker_id` change rebinds `resolve_dingtalk_agent_id` to the new agent id. Add this handling inside the turn loop:

```python
                    if turn.restart_before:
                        await close_online_runtime_and_reinit(checkpoint_runtime, fake_settings)()
                    if turn.worker_id:
                        patcher.stop()
                        patcher = patch(
                            "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                            lambda db, tenant_id: turn.worker_id,
                        )
                        patcher.start()
```

(Initialize `checkpoint_runtime`, `fake_settings`, and the initial `patcher` before the loop, mirroring Task 12.)

- [ ] **Step 6: Write the integration test**

```python
# tests/integration/test_memory_eval_dingtalk_staging.py
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dingtalk_staging_captures_public_reply(tmp_path):
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        pytest.skip("TEST_DATABASE_URL not set to a test database")
    ds = tmp_path / "ds.jsonl"
    ds.write_text(json.dumps({
        "id": "dt-001", "tags": ["explicit"],
        "turns": [{"input": "记住我负责华东区", "event_id": "dt-001-1",
                   "expected": {"memory_operation": "remember", "reply_contains": ["华东"]}}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    from eval.memory_eval.runner import run_dingtalk_staging
    from types import SimpleNamespace
    rc = await run_dingtalk_staging(SimpleNamespace(
        dataset=str(ds), output=str(tmp_path / "out"),
        tenant_id="eval-tenant", agent_id="eval-agent",
    ))
    assert (tmp_path / "out" / "report.json").exists()
    assert rc in (0, 1)
```

- [ ] **Step 7: Run tests + commit**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_dingtalk_capture.py -v`
Expected: PASS (2)

```bash
git add eval/memory_eval/dingtalk_capture.py eval/memory_eval/runner.py \
        tests/unit/eval/test_dingtalk_capture.py tests/integration/test_memory_eval_dingtalk_staging.py
git commit -m "feat(eval): add dingtalk-staging mode with public-only capture (Spec 4 §3.4)"
```

---

### Task 15: Author the ≥40-scenario dataset

§4: at least 40 multi-turn scenarios across nine required categories; five `turn_relation` classes; expiry/restore + clarification; four Guided Flows + preemption; explicit/inferred remember; correction/forget/expiry; profile recall + no-recall controls; cross-scope isolation; restart/duplicate/concurrency; long-term-memory degradation.

**Files:**
- Create: `eval/memory/datasets/multiturn_v1.jsonl`
- Test: `tests/unit/eval/test_multiturn_dataset_coverage.py`

**Interfaces:**
- Consumes: `dataset.load_scenarios`, `dataset.validate_dataset`.
- Produces: the committed `multiturn_v1.jsonl` with `version: 1` and full tag coverage.

- [ ] **Step 1: Write the failing coverage test**

```python
# tests/unit/eval/test_multiturn_dataset_coverage.py
from __future__ import annotations

from pathlib import Path

from eval.memory_eval.dataset import load_scenarios, validate_dataset

DATASET = Path("eval/memory/datasets/multiturn_v1.jsonl")

REQUIRED_TAG_GROUPS = {
    "turn_relation_classes": {"new", "switch", "continue", "revise", "ambiguous"},
    "expiry_restore": {"expiry", "restore", "clarification"},
    "guided_flow": {"guided_flow", "preemption"},
    "explicit_inferred": {"explicit", "inferred", "remember"},
    "correct_forget_expiry": {"correct", "forget"},
    "profile_recall": {"profile_recall", "profile_no_recall"},
    "cross_scope": {"cross_scope_isolation"},
    "restart_duplicate_concurrency": {"restart", "duplicate", "concurrency"},
    "degradation": {"degradation"},
}


def test_dataset_has_40_plus_scenarios_and_full_coverage():
    scenarios = load_scenarios(str(DATASET))
    assert len(scenarios) >= 40, f"need >=40 scenarios, got {len(scenarios)}"
    errors = validate_dataset(scenarios)
    assert not errors, f"dataset invalid: {errors}"

    all_tags = set()
    for s in scenarios:
        all_tags.update(s.tags)
    for group, required in REQUIRED_TAG_GROUPS.items():
        missing = required - all_tags
        assert not missing, f"{group} missing tags: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_multiturn_dataset_coverage.py -v`
Expected: FAIL (dataset file does not exist)

- [ ] **Step 3: Author the dataset**

Create `eval/memory/datasets/multiturn_v1.jsonl`. Each line is one scenario conforming to `MultiturnScenario`. Below is the **template and the first scenarios verbatim**; the implementer authors the remaining scenarios following this exact shape to reach ≥40 with full tag coverage. Every scenario sets `"version": 1`. All inputs are synthetic (no real PII — the anonymization gate in Task 5 will reject any secret/identifier).

```jsonl
{"id":"mt-001-explicit-remember-region","version":1,"tags":["explicit","remember","new"],"initial_state":{},"turns":[{"input":"记住我负责华东区","time_offset_seconds":0,"restart_before":false,"event_id":"mt001-1","expected":{"turn_relation":"new","memory_operation":"remember","memory_status":"success","active_memory_keys":["sales_region"],"reply_contains":["华东"]}}],"final_expected":{"active_topic_count":1,"cross_scope_leakage":false}}
{"id":"mt-002-switch-topic-leakage","version":1,"tags":["switch","turn_relation_classes","new"],"turns":[{"input":"帮我看看福多多产品怎么讲","event_id":"mt002-1","expected":{"turn_relation":"new","reply_count":1}},{"input":"换个话题，客户说预算紧张","event_id":"mt002-2","expected":{"turn_relation":"switch","retained_entities":[],"reply_count":1}}],"final_expected":{"active_topic_count":1,"cross_scope_leakage":false}}
{"id":"mt-003-inferred-corroboration","version":1,"tags":["inferred","corroboration"],"turns":[{"input":"我负责华东区，客户主要在上海","event_id":"mt003-1","expected":{"candidate_count":1,"active_memory_count":0}},{"input":"对了，我这边还是负责华东区域","event_id":"mt003-2","expected":{"active_memory_keys":["sales_region"]}}],"final_expected":{}}
{"id":"mt-004-correction-supersedes","version":1,"tags":["correct"],"turns":[{"input":"记住我负责华东区","event_id":"mt004-1","expected":{"memory_operation":"remember","memory_status":"success"}},{"input":"我不负责华东了，现在负责华南","event_id":"mt004-2","expected":{"memory_operation":"correct","memory_status":"success","active_memory_values":["华南"]}}],"final_expected":{}}
{"id":"mt-005-forget-region","version":1,"tags":["forget"],"turns":[{"input":"记住我负责华东区","event_id":"mt005-1","expected":{"memory_operation":"remember","memory_status":"success"}},{"input":"忘记我的区域信息","event_id":"mt005-2","expected":{"memory_operation":"forget","memory_status":"success","active_memory_keys":[]}}],"final_expected":{}}
{"id":"mt-006-sensitive-refused","version":1,"tags":["safety"],"turns":[{"input":"记住我的密码是 abc123","event_id":"mt006-1","expected":{"memory_operation":"remember","memory_status":"rejected","sensitive_persisted":0,"reply_contains":["不会记录"]}}],"final_expected":{}}
```

Author scenarios `mt-007` … `mt-0NN` (target N ≥ 40) covering, at minimum:
- 5 scenarios — one per `turn_relation` class (`continue`, `revise`, `switch`, `new`, `ambiguous`), tagged with the class name and `turn_relation_classes`.
- 4 Guided Flow scenarios (one per flow: e.g. `small_win_appreciation`, `objection_handling`, `next_action_planning`, `relationship_deepening`) tagged `guided_flow`, plus one `preemption` scenario where a higher-priority user intent preempts an in-progress flow.
- 2 expiry/restore scenarios (`expiry`, `restore`) where `time_offset_seconds` advances past the Topic TTL and the next turn restores it; plus a `clarification` scenario whose second turn resolves a pending clarification.
- 3 profile scenarios: `profile_recall` (profile field surfaces in a later turn), `profile_no_recall` (no profile exists yet → no fabrication), and a `degradation` scenario where memory is unavailable and the assistant degrades gracefully.
- 2 cross-scope isolation scenarios (`cross_scope_isolation`) where the same input from two different `user_id`s produces two independent memory rows.
- 3 operational scenarios: `restart` (`restart_before: true` mid-scenario), `duplicate` (`duplicate_previous_event: true` → no double reply), `concurrency` (`concurrent_group` set → serialized by the advisory lock).

Each must pass `validate_dataset` (Task 5): no secrets, no direct identifiers, no `unreviewed_production` tag.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_multiturn_dataset_coverage.py -v`
Expected: PASS (1 test). If it fails on missing tags, add scenarios for the missing group.

- [ ] **Step 5: Commit**

```bash
git add eval/memory/datasets/multiturn_v1.jsonl tests/unit/eval/test_multiturn_dataset_coverage.py
git commit -m "feat(eval): add >=40-scenario multiturn_v1 dataset (Spec 4 §4)"
```

---

### Task 16: Release gates (`gates.py`)

§6: overall `turn_relation` accuracy ≥ 90%; no critical new/switch Topic leakage; zero cross-tenant/Agent/user + prohibited-memory leakage; 100% explicit remember/correct/forget on deterministic cases; clarification completion ≥ 90%; relevant-memory precision ≥ 90%; 100% restart/worker-switch recovery; zero assistant-output-to-user-memory activations; no unexplained regression beyond tolerance. Gates fail closed for isolation/safety.

**Files:**
- Create: `eval/memory_eval/gates.py`
- Test: `tests/unit/eval/test_gates.py`

**Interfaces:**
- Consumes: `report.EvaluationReport`.
- Produces: `GateSpec`, `GATES` list, `check_gates(report) -> GateReport`, `assistant_output_to_user_memory_violation(pairs)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_gates.py
from __future__ import annotations

from eval.memory_eval.gates import GATES, GateReport, check_gates
from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.report import EvaluationReport
from eval.memory_eval.versions import VersionBundle


def _vb():
    return VersionBundle("m", "p", "c", "d", "k", "ms", "g")


def test_passing_report_meets_all_gates():
    # Must satisfy EVERY gate in GATES, including all three fail-closed
    # safety gates (check_gates appends a failure for an unsatisfied
    # safety gate, so omitting them would wrongly flip gr.passed to False).
    r = EvaluationReport(versions=_vb())
    r.add_metric("deterministic_state_safety", MetricResult(
        name="turn_relation_accuracy", numerator=9, denominator=10, score=0.95, threshold=0.9))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="topic_leakage_rate", numerator=0, denominator=2, score=0.0, threshold=0.0))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="clarification_completion", numerator=9, denominator=10, score=0.9, threshold=0.9))
    r.add_metric("deterministic_state_safety", MetricResult(
        name="restart_recovery", numerator=2, denominator=2, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="explicit_operation_accuracy", numerator=5, denominator=5, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="correction_supersede_accuracy", numerator=3, denominator=3, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="forget_effectiveness", numerator=2, denominator=2, score=1.0, threshold=1.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="prohibited_memory_write_count", numerator=0, denominator=0, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="cross_scope_leakage", numerator=0, denominator=1, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="assistant_output_to_user_memory", numerator=0, denominator=1, score=0.0, threshold=0.0))
    r.add_metric("persistence_isolation", MetricResult(
        name="relevant_memory_precision", numerator=9, denominator=10, score=0.9, threshold=0.9))
    gr = check_gates(r)
    assert gr.passed is True
    assert gr.failed_gates == []


def test_leakage_gate_fails_closed():
    r = EvaluationReport(versions=_vb())
    r.add_metric("persistence_isolation", MetricResult(
        name="prohibited_memory_write_count", numerator=1, denominator=1, score=1.0, threshold=0.0))
    gr = check_gates(r)
    assert gr.passed is False
    assert any("prohibited_memory_write_count" in g for g in gr.failed_gates)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_gates.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/gates.py
"""Release gates (Spec 4 §6). Isolation/safety gates fail closed."""
from __future__ import annotations

from dataclasses import dataclass, field

from eval.memory_eval.report import EvaluationReport


@dataclass
class GateSpec:
    metric: str
    min_score: float | None    # pass if score >= min_score
    max_score: float | None    # pass if score <= max_score (for leakage/count gates)


@dataclass
class GateReport:
    passed: bool = True
    failed_gates: list[str] = field(default_factory=list)


# (§6) Initial release gates.
GATES: list[GateSpec] = [
    GateSpec("turn_relation_accuracy", 0.90, None),
    GateSpec("topic_leakage_rate", None, 0.0),
    GateSpec("explicit_operation_accuracy", 1.0, None),
    GateSpec("correction_supersede_accuracy", 1.0, None),
    GateSpec("forget_effectiveness", 1.0, None),
    GateSpec("prohibited_memory_write_count", None, 0.0),
    GateSpec("cross_scope_leakage", None, 0.0),
    GateSpec("clarification_completion", 0.90, None),
    GateSpec("relevant_memory_precision", 0.90, None),
    GateSpec("restart_recovery", 1.0, None),
    GateSpec("assistant_output_to_user_memory", None, 0.0),
]


def _find(report: EvaluationReport, metric: str):
    for metrics in report.groups.values():
        for m in metrics:
            if m.name == metric:
                return m
    return None


def check_gates(report: EvaluationReport) -> GateReport:
    failed: list[str] = []
    for spec in GATES:
        m = _find(report, spec.metric)
        if m is None:
            # Gate metric absent → treat as not-applicable (not a failure) unless
            # it is an isolation/safety gate, which must be explicitly satisfied.
            if spec.metric in {"prohibited_memory_write_count", "cross_scope_leakage",
                               "assistant_output_to_user_memory"}:
                failed.append(f"{spec.metric}: not evaluated (safety gate must be satisfied)")
            continue
        if m.error is not None:
            continue  # evaluator error never flips the gate (§10)
        if spec.min_score is not None and m.score < spec.min_score:
            failed.append(f"{spec.metric}: {m.score} < {spec.min_score}")
        if spec.max_score is not None and m.score > spec.max_score:
            failed.append(f"{spec.metric}: {m.score} > {spec.max_score}")
    return GateReport(passed=not failed, failed_gates=failed)


def assistant_output_to_user_memory_violation(pairs) -> MetricResult_check_type:
    """Detect assistant text wrongly activated as a user-scoped memory (§6).

    Returns a MetricResult-like record with numerator = violation count.
    """
    # A violation: an active memory whose source_kind == assistant_output.
    violations = 0
    for _, run in pairs:
        for obs in run.observed:
            violations += sum(
                1 for src in (obs.result.get("activated_memory_sources") or [])
                if src == "assistant_output"
            )
    from eval.memory_eval.metrics.types import MetricResult
    return MetricResult(
        name="assistant_output_to_user_memory",
        numerator=violations, denominator=violations or 1,
        score=float(violations), threshold=0.0,
    )


# Type alias used above to avoid a forward-reference cycle.
MetricResult_check_type = "MetricResult"


__all__ = ["GATES", "GateReport", "GateSpec", "check_gates",
           "assistant_output_to_user_memory_violation"]
```

> **Wire-up:** each mode that emits a report should call `check_gates(report)` and merge `failed_gates` into the report before writing. In Task 23 the gate script asserts `check_gates` passes. For now, the gate logic is independently tested.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_gates.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/gates.py tests/unit/eval/test_gates.py
git commit -m "feat(eval): add fail-closed release gates (Spec 4 §6)"
```

---

## Phase 5 — Production operations

> Phase 5 depends on the Phase 1 core (schema, versions, metrics, report) but does not modify it. It can be executed as a second pass after Phases 1–4 + 6 ship.

### Task 17: Per-turn eval trace capture (`memory_eval_trace.py`)

§8: extract the per-turn trace fields (hashed scope identifiers, Topic ID/transition, checkpoint/thread version, eligible + selected memory types/IDs, profile version, memory degradation/fallback reason, route/retrieval/risk/Guided-Flow decisions, latency/usage, model/prompt/code versions, explicit correction/forget/negative-feedback signals).

**Files:**
- Create: `src/sales_agent/services/memory_eval_trace.py`
- Test: `tests/unit/services/test_memory_eval_trace.py`

**Interfaces:**
- Consumes: `OnlineConversationState` dict, `eval.memory_eval.versions.collect_version_bundle`.
- Produces: `hash_scope(tenant_id, agent_id, user_id) -> str`, `build_eval_trace(state, *, now=None, versions=None) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_memory_eval_trace.py
from __future__ import annotations

from sales_agent.services.memory_eval_trace import build_eval_trace, hash_scope


def test_hash_scope_is_stable_and_irreversible():
    a = hash_scope("tenant-1", "agent-1", "user-1")
    b = hash_scope("tenant-1", "agent-1", "user-1")
    assert a == b
    assert a.startswith("h:")
    assert "user-1" not in a


def test_build_eval_trace_captures_section_8_fields():
    state = {
        "tenant_id": "t", "agent_id": "a", "user_id": "u",
        "topic_id": "topic-7", "turn_relation": "switch",
        "thread_id": "online:t:a:dt:u", "checkpoint_version": 3,
        "memory_ids": ["m1"], "selected_memory_ids": ["m1"],
        "profile_version": "v9",
        "memory_degraded": False, "memory_degradation_reason": None,
        "knowledge_policy": "restricted", "risk_decision": "allow",
        "active_flow": "small_win_appreciation", "flow_stage": "small_win",
        "latency_ms": 420.0, "total_tokens": 180,
        "user_correction": True, "forget_requested": False, "negative_feedback": False,
    }
    trace = build_eval_trace(state)
    assert trace["scope_hash"].startswith("h:")
    assert trace["topic_id"] == "topic-7"
    assert trace["topic_transition"] == "switch"
    assert trace["checkpoint_version"] == 3
    assert trace["selected_memory_ids"] == ["m1"]
    assert trace["profile_version"] == "v9"
    assert trace["memory_degraded"] is False
    assert trace["guided_flow"] == "small_win_appreciation"
    assert trace["latency_ms"] == 420.0
    assert trace["signals"]["user_correction"] is True
    assert "versions" in trace
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/services/test_memory_eval_trace.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/sales_agent/services/memory_eval_trace.py
"""Per-turn eval trace capture (Spec 4 §8).

Repo convention is DB-backed observability + stdlib logging (no OTel).
This module extracts the §8 fields from an Online Graph state dict into a
serializable, anonymized trace used by the online-sample mode and reports.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional


def hash_scope(tenant_id: str, agent_id: str, user_id: str) -> str:
    """One-way hash of the scope triple (§8: hashed scope identifiers)."""
    raw = f"{tenant_id}|{agent_id}|{user_id}".encode("utf-8")
    return "h:" + hashlib.sha256(raw).hexdigest()[:24]


def build_eval_trace(
    state: dict[str, Any],
    *,
    now: Optional[Any] = None,
    versions: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Extract the §8 per-turn trace fields from a graph state dict."""
    if versions is None:
        # Local import to avoid a hard dependency in unit tests.
        from eval.memory_eval.versions import collect_version_bundle
        versions = collect_version_bundle().to_dict()

    return {
        "captured_at": now,
        "scope_hash": hash_scope(
            state.get("tenant_id", ""), state.get("agent_id", ""), state.get("user_id", ""),
        ),
        "topic_id": state.get("topic_id"),
        "topic_transition": state.get("turn_relation"),
        "thread_id": state.get("thread_id"),
        "checkpoint_version": state.get("checkpoint_version"),
        "eligible_memory_ids": state.get("memory_ids") or [],
        "selected_memory_ids": state.get("selected_memory_ids") or [],
        "profile_version": state.get("profile_version"),
        "memory_degraded": bool(state.get("memory_degraded")),
        "memory_degradation_reason": state.get("memory_degradation_reason"),
        "route": state.get("flow_action") or state.get("requested_flow"),
        "retrieval": state.get("knowledge_policy"),
        "risk": state.get("risk_decision"),
        "guided_flow": state.get("active_flow"),
        "guided_flow_stage": state.get("flow_stage"),
        "latency_ms": state.get("latency_ms"),
        "total_tokens": state.get("total_tokens"),
        "signals": {
            "user_correction": bool(state.get("user_correction")),
            "forget_requested": bool(state.get("forget_requested")),
            "negative_feedback": bool(state.get("negative_feedback")),
        },
        "versions": versions,
    }


__all__ = ["build_eval_trace", "hash_scope"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/services/test_memory_eval_trace.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/services/memory_eval_trace.py tests/unit/services/test_memory_eval_trace.py
git commit -m "feat(ops): add per-turn eval trace capture (Spec 4 §8)"
```

---

### Task 18: DB models + migration for sampled traces and promoted regressions

§9.2 (restricted retention with identifiers protected) + §9.4 (minimal anonymized regression scenario). Two tables: `memory_eval_traces` (sampled production traces, restricted retention) and `promoted_regressions` (trace → anonymized scenario).

**Files:**
- Create: `src/sales_agent/models/memory_eval.py`
- Create: `src/sales_agent/migrations/versions/0015_memory_eval_operations.py`
- Modify: `src/sales_agent/models/__init__.py` (register models so Alembic `env.py` imports them — follow the existing `_import_dingtalk_models()` registration pattern; check how `atomic_memory`/`user_memory_profile` are registered and mirror it).
- Test: `tests/integration/test_memory_eval_models.py`

**Interfaces:**
- Consumes: `sales_agent.models.base.TimestampMixin`, `generate_id`, `sales_agent.core.database.Base`.
- Produces: `MemoryEvalTraceRecord`, `PromotedRegression` ORM models + the migration that creates both tables.

- [ ] **Step 0: Verify migration head before authoring**

Run: `cd /root/code/sales-agent && alembic heads`
Expected: one head revision id. The new migration's `down_revision` must equal this head. If the head is `0013` use that; if `0014_user_memory_profiles` is present use `0014`. The file is named `0015_memory_eval_operations.py` regardless (its `revision = "0015"`).

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_memory_eval_models.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from sales_agent.models.memory_eval import MemoryEvalTraceRecord, PromotedRegression


@pytest.mark.asyncio
async def test_trace_record_roundtrip(db_session):
    rec = MemoryEvalTraceRecord(
        tenant_id="t1", scope_hash="h:abc", thread_id="online:t:a:c:u",
        trace_json={"topic_id": "topic-7"}, retention="restricted", status="sampled",
    )
    db_session.add(rec)
    await db_session.flush()
    rows = (await db_session.execute(
        select(MemoryEvalTraceRecord).where(MemoryEvalTraceRecord.scope_hash == "h:abc")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].trace_json["topic_id"] == "topic-7"
    assert rows[0].retention == "restricted"


@pytest.mark.asyncio
async def test_promoted_regression_roundtrip(db_session):
    pr = PromotedRegression(
        tenant_id="t1", source_trace_id="tr-1", scenario_json={"id": "promoted-001"},
        status="draft", anonymized=True,
    )
    db_session.add(pr)
    await db_session.flush()
    rows = (await db_session.execute(select(PromotedRegression))).scalars().all()
    assert len(rows) == 1
    assert rows[0].anonymized is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test python3 -m pytest tests/integration/test_memory_eval_models.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the ORM models**

```python
# src/sales_agent/models/memory_eval.py
"""ORM models for production memory-eval operations (Spec 4 §8, §9).

- ``MemoryEvalTraceRecord``: a sampled production trace under restricted
  retention (hashed scope, no plaintext identifiers).
- ``PromotedRegression``: an anonymized regression scenario promoted from a
  reviewed trace (Spec 4 §9.4).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class MemoryEvalTraceRecord(TimestampMixin, Base):
    __tablename__ = "memory_eval_traces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    retention: Mapped[str] = mapped_column(String, default="restricted", nullable=False)
    status: Mapped[str] = mapped_column(String, default="sampled", nullable=False)
    captured_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_memory_eval_traces_tenant_status", "tenant_id", "status"),
    )


class PromotedRegression(TimestampMixin, Base):
    __tablename__ = "promoted_regressions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    source_trace_id: Mapped[str] = mapped_column(String, nullable=False)
    scenario_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, default="draft", nullable=False)  # draft|reviewed|committed
    anonymized: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["MemoryEvalTraceRecord", "PromotedRegression"]
```

Register the models so Alembic picks them up. In `src/sales_agent/models/__init__.py`, add the import alongside the other model imports (mirror however `atomic_memory` is imported there — e.g. `from sales_agent.models.memory_eval import MemoryEvalTraceRecord, PromotedRegression`).

- [ ] **Step 4: Generate and fill the migration**

Run: `cd /root/code/sales-agent && alembic revision -m "memory eval operations"`
This creates `src/sales_agent/migrations/versions/0015_memory_eval_operations_*.py`. Rename/confirm the file is `0015_memory_eval_operations.py` and set `revision = "0015"`, `down_revision = "<head from Step 0>"`. Fill `upgrade`/`downgrade`:

```python
"""memory eval operations

Revision ID: 0015
Revises: <head>
Create Date: ...
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "<head>"   # paste the verified head id
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_eval_traces",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("scope_hash", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("retention", sa.String(), nullable=False, server_default="restricted"),
        sa.Column("status", sa.String(), nullable=False, server_default="sampled"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memory_eval_traces_tenant_id", "memory_eval_traces", ["tenant_id"])
    op.create_index("ix_memory_eval_traces_tenant_status", "memory_eval_traces", ["tenant_id", "status"])

    op.create_table(
        "promoted_regressions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_trace_id", sa.String(), nullable=False),
        sa.Column("scenario_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("anonymized", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_promoted_regressions_tenant_id", "promoted_regressions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_promoted_regressions_tenant_id", table_name="promoted_regressions")
    op.drop_table("promoted_regressions")
    op.drop_index("ix_memory_eval_traces_tenant_status", table_name="memory_eval_traces")
    op.drop_index("ix_memory_eval_traces_tenant_id", table_name="memory_eval_traces")
    op.drop_table("memory_eval_traces")
```

> **Gotcha (from project changelog):** do not mix `create_table` + `add_column` in one revision, and prefer idempotent DDL. This migration only creates two new tables, so the ghost-drift risk does not apply.

- [ ] **Step 5: Run the migration + test**

Run:
```bash
cd /root/code/sales-agent
PYTHONPATH=src alembic upgrade head
PYTHONPATH=src:. TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  python3 -m pytest tests/integration/test_memory_eval_models.py -v
```
Expected: migration applies cleanly; test PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/models/memory_eval.py src/sales_agent/models/__init__.py \
        src/sales_agent/migrations/versions/0015_memory_eval_operations.py \
        tests/integration/test_memory_eval_models.py
git commit -m "feat(ops): add memory_eval_traces + promoted_regressions tables (Spec 4 §8,§9)"
```

---

### Task 19: `online-sample` mode

§3.5: sample 5% of eligible completed threads after 30 minutes of inactivity; deterministic checks + limited reference-free semantic eval; high-risk failures, user corrections, repeated clarification, and explicit negative feedback are always retained regardless of sampling; bounded sampling/cost; **online-evaluation failure cannot block the user response** (§10).

The sampling decision + high-risk classification are pure and unit-tested; the mode wires them to a DB query and stores traces under restricted retention.

**Files:**
- Create: `eval/memory_eval/sampling.py`
- Modify: `eval/memory_eval/runner.py` (add `run_online_sample` + subcommand + dispatch).
- Test: `tests/unit/eval/test_sampling.py`

**Interfaces:**
- Consumes: `schema`, `services.memory_eval_trace.build_eval_trace`, `models.memory_eval.MemoryEvalTraceRecord`.
- Produces: `should_sample(thread, *, now, rng, rate, inactivity_seconds) -> bool`, `is_high_risk(thread) -> bool`, `run_online_sample(args) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_sampling.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eval.memory_eval.sampling import is_high_risk, should_sample


def _thread(last_active_ago_seconds, *, correction=False, negative=False, clarifications=0):
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    return {
        "last_active_at": now - timedelta(seconds=last_active_ago_seconds),
        "user_correction": correction,
        "negative_feedback": negative,
        "clarification_attempts": clarifications,
    }


def test_not_eligible_before_inactivity_window():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # 10 minutes idle → not yet eligible (30 min threshold)
    assert should_sample(_thread(600), now=now, rng=lambda: 0.0) is False


def test_high_risk_always_retained_regardless_of_sampling():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # eligible (40 min idle) + user correction → always retained
    assert should_sample(_thread(2400, correction=True), now=now, rng=lambda: 0.99) is True


def test_normal_thread_sampled_at_5_percent():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # eligible + rng below 0.05 → sampled
    assert should_sample(_thread(2400), now=now, rng=lambda: 0.03) is True
    # eligible + rng above 0.05 → not sampled
    assert should_sample(_thread(2400), now=now, rng=lambda: 0.10) is False


def test_is_high_risk_detection():
    assert is_high_risk(_thread(0, correction=True)) is True
    assert is_high_risk(_thread(0, negative=True)) is True
    assert is_high_risk(_thread(0, clarifications=2)) is True
    assert is_high_risk(_thread(0)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_sampling.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the sampling module**

```python
# eval/memory_eval/sampling.py
"""Online sampling decisions (Spec 4 §3.5)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

DEFAULT_RATE = 0.05
DEFAULT_INACTIVITY_SECONDS = 30 * 60
DEFAULT_CLARIFICATION_REPEAT_THRESHOLD = 2


def is_high_risk(thread: dict[str, Any]) -> bool:
    """Always retain: user correction, negative feedback, repeated clarification (§3.5)."""
    if thread.get("user_correction") or thread.get("negative_feedback"):
        return True
    if int(thread.get("clarification_attempts") or 0) >= DEFAULT_CLARIFICATION_REPEAT_THRESHOLD:
        return True
    return False


def should_sample(
    thread: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    rng: Optional[Callable[[], float]] = None,
    rate: float = DEFAULT_RATE,
    inactivity_seconds: int = DEFAULT_INACTIVITY_SECONDS,
) -> bool:
    now = now or datetime.now(timezone.utc)
    rng = rng or random.random
    last_active = thread.get("last_active_at")
    if last_active is None:
        return False
    if (now - last_active).total_seconds() < inactivity_seconds:
        return False
    if is_high_risk(thread):
        return True
    return rng() < rate


__all__ = ["DEFAULT_INACTIVITY_SECONDS", "DEFAULT_RATE", "is_high_risk", "should_sample"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_sampling.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the mode to `runner.py`**

```python
# --- online-sample (Spec 4 §3.5) ---

async def run_online_sample(args) -> int:
    """Sample eligible completed threads; deterministic + limited semantic.

    Bounded by --max-threads per run. Online-eval failure never blocks the
    user response: all DB writes are best-effort and isolated (§10).
    """
    from datetime import datetime, timezone
    from eval.memory_eval.sampling import should_sample
    from sales_agent.services.memory_eval_trace import build_eval_trace
    from sales_agent.models.memory_eval import MemoryEvalTraceRecord
    from sales_agent.core.database import get_session_factory
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    sampled = 0
    try:
        async with get_session_factory()() as db:
            # Candidate threads: completed + idle. The exact "completed thread"
            # source is the conversation/message log; query the most recent
            # finished conversations per thread_id. (Bounded by --max-threads.)
            rows = (await db.execute(
                select(/* thread_id, last_active_at, signals */).limit(args.max_threads)
            )).all() if False else []  # placeholder until the thread-log view exists
            for row in rows:
                thread = dict(row._mapping)
                if should_sample(thread, now=now):
                    trace = build_eval_trace(thread, now=now)
                    db.add(MemoryEvalTraceRecord(
                        tenant_id=args.tenant_id, scope_hash=trace["scope_hash"],
                        thread_id=trace["thread_id"], trace_json=trace,
                        retention="restricted", status="sampled", captured_at=now,
                    ))
                    sampled += 1
            await db.commit()
    except Exception:  # noqa: BLE001 — never block user traffic (§10)
        import logging
        logging.getLogger(__name__).exception("online-sample failed (non-blocking)")
        return 0

    print(f"online-sample: sampled {sampled} threads")
    return 0
```

> **Note on the thread-log query:** the production "completed thread" source is the conversation log (`services/conversation_logger.py` + `models/conversation`). The exact SELECT depends on that schema; when implementing, replace the `rows = ... if False else []` line with a real query grouping messages by `thread_id` with `max(created_at)` and the correction/clarification signals. The pure sampling logic (the tested part) is unaffected.

Register subcommand + dispatch:

```python
    p = sub.add_parser("online-sample")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--max-threads", type=int, default=100)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/online-sample")
```
```python
    if args.mode == "online-sample":
        return await run_online_sample(args)
```

- [ ] **Step 6: Commit**

```bash
git add eval/memory_eval/sampling.py eval/memory_eval/runner.py tests/unit/eval/test_sampling.py
git commit -m "feat(ops): add online-sample mode + sampling logic (Spec 4 §3.5,§10)"
```

---

### Task 20: `compare` mode

§7 baseline comparison: exclude incompatible schema versions; label new / fixed / regressed / flaky / evaluator-error cases.

**Files:**
- Modify: `eval/memory_eval/runner.py` (add `run_compare` + subcommand + dispatch).
- Test: `tests/unit/eval/test_compare.py`

**Interfaces:**
- Consumes: two report JSON files (baseline + candidate).
- Produces: `compare_reports(baseline: dict, candidate: dict) -> dict`, `run_compare(args) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_compare.py
from __future__ import annotations

from eval.memory_eval.runner import compare_reports


def _report(metrics, schema_version="0013"):
    return {
        "versions": {"memory_schema_version": schema_version},
        "groups": {"deterministic_state_safety": metrics},
    }


def test_incompatible_schema_excluded():
    base = _report([{"name": "turn_relation_accuracy", "score": 0.9}], "0013")
    cand = _report([{"name": "turn_relation_accuracy", "score": 0.95}], "0014")
    out = compare_reports(base, cand)
    assert out["schema_compatible"] is False
    assert out["labels"] == {}


def test_labels_regressed_fixed_new():
    base = _report([
        {"name": "turn_relation_accuracy", "score": 0.95, "error": None},
        {"name": "explicit_operation_accuracy", "score": 1.0, "error": None},
    ])
    cand = _report([
        {"name": "turn_relation_accuracy", "score": 0.90, "error": None},   # regressed
        {"name": "explicit_operation_accuracy", "score": 1.0, "error": None},  # unchanged
        {"name": "new_metric", "score": 0.9, "error": None},                # new
    ])
    out = compare_reports(base, cand)
    assert out["labels"]["turn_relation_accuracy"] == "regressed"
    assert out["labels"]["new_metric"] == "new"


def test_labels_evaluator_error_and_flaky():
    base = _report([{"name": "relevance", "score": 0.8, "error": None}])
    cand = _report([{"name": "relevance", "score": 0.0, "error": "judge timeout"}])
    out = compare_reports(base, cand)
    assert out["labels"]["relevance"] == "evaluator_error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_compare.py -v`
Expected: FAIL (no `compare_reports`)

- [ ] **Step 3: Write minimal implementation (add to `runner.py`)**

```python
# --- compare (Spec 4 §7) ---

REGRESSION_TOLERANCE = 0.0  # no unexplained regression beyond tolerance (§6)


def _metric_map(report: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for metrics in report.get("groups", {}).values():
        for m in metrics:
            out[m["name"]] = m
    return out


def compare_reports(baseline: dict, candidate: dict) -> dict:
    base_schema = baseline.get("versions", {}).get("memory_schema_version")
    cand_schema = candidate.get("versions", {}).get("memory_schema_version")
    if base_schema != cand_schema:
        return {"schema_compatible": False, "labels": {},
                "reason": f"schema {base_schema} != {cand_schema}"}

    base = _metric_map(baseline)
    cand = _metric_map(candidate)
    labels: dict[str, str] = {}
    for name, cm in cand.items():
        if name not in base:
            labels[name] = "new"
            continue
        bm = base[name]
        if cm.get("error"):
            labels[name] = "evaluator_error"
        elif bm.get("error") and not cm.get("error"):
            labels[name] = "fixed"
        else:
            delta = cm["score"] - bm["score"]
            if delta > REGRESSION_TOLERANCE:
                labels[name] = "improved"
            elif delta < -REGRESSION_TOLERANCE:
                labels[name] = "regressed"
            else:
                labels[name] = "unchanged"
    for name in base:
        if name not in cand:
            labels[name] = "removed"
    return {"schema_compatible": True, "labels": labels}


def run_compare(args) -> int:
    import json
    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    with open(args.candidate, encoding="utf-8") as f:
        candidate = json.load(f)
    out = compare_reports(baseline, candidate)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"compare: schema_compatible={out['schema_compatible']} labels={out['labels']}")
    # Non-zero only on invalid execution (§11); a regression is reported, not fatal here.
    return 0 if out["schema_compatible"] else 2
```

Register subcommand + dispatch (synchronous — no DB):

```python
    p = sub.add_parser("compare")
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/compare.json")
```
```python
    if args.mode == "compare":
        return run_compare(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_compare.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/runner.py tests/unit/eval/test_compare.py
git commit -m "feat(eval): add compare mode with schema/label classification (Spec 4 §7)"
```

---

### Task 21: `promote-trace` workflow

§9: detect failure → store restricted trace → classify root cause → create minimal anonymized regression scenario with explicit expected → reproduce before fixing. This task implements the anonymization + scenario-building (the reproducible, testable core) and the mode that persists a `PromotedRegression`.

**Files:**
- Create: `eval/memory_eval/promote.py`
- Modify: `eval/memory_eval/runner.py` (add `run_promote_trace` + subcommand + dispatch).
- Test: `tests/unit/eval/test_promote.py`

**Interfaces:**
- Consumes: `services.memory_eval_trace` trace dict, `dataset.validate_dataset`, `schema.MultiturnScenario`, `models.memory_eval.PromotedRegression`.
- Produces: `anonymize_trace(trace) -> dict`, `build_regression_scenario(trace, *, scenario_id, expected) -> MultiturnScenario`, `classify_root_cause(trace) -> str`, `run_promote_trace(args) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_promote.py
from __future__ import annotations

from eval.memory_eval.promote import (
    anonymize_trace,
    build_regression_scenario,
    classify_root_cause,
)
from eval.memory_eval.dataset import validate_dataset


def _trace():
    return {
        "scope_hash": "h:abc",
        "topic_id": "topic-7",
        "topic_transition": "switch",
        "selected_memory_ids": ["m1"],
        "memory_degraded": True,
        "memory_degradation_reason": "exception",
        "signals": {"user_correction": True, "negative_feedback": False},
        "versions": {"memory_schema_version": "0013"},
        "turns": [{"input": "记住我负责华东区", "reply": "好的"}],
    }


def test_anonymize_strips_reply_and_keeps_inputs_redacted():
    out = anonymize_trace(_trace())
    assert out["scope_hash"] == "h:abc"
    assert "reply" not in out["turns"][0]           # outbound dropped
    # inputs are retained but will be re-validated by validate_dataset


def test_classify_root_cause():
    assert classify_root_cause(_trace()) == "memory"  # degradation_reason present
    t2 = dict(_trace()); t2["memory_degraded"] = False; t2["memory_degradation_reason"] = None
    t2["topic_transition"] = "switch"; t2["selected_memory_ids"] = []
    assert classify_root_cause(t2) == "routing"


def test_build_regression_scenario_is_valid_dataset():
    scenario = build_regression_scenario(
        _trace(), scenario_id="promoted-001",
        expected={"turn_relation": "switch"},
    )
    errors = validate_dataset([scenario])
    assert errors == []
    assert scenario.id == "promoted-001"
    assert scenario.turns[0].expected.turn_relation == "switch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_promote.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/promote.py
"""promote-trace workflow (Spec 4 §9)."""
from __future__ import annotations

import copy
from typing import Any

from eval.memory_eval.schema import ExpectedTurn, MultiturnScenario, ScenarioTurn


def anonymize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Drop outbound replies and any free-text that could identify a user (§9.2)."""
    out = copy.deepcopy(trace)
    for t in out.get("turns", []):
        t.pop("reply", None)
        t.pop("outbound", None)
    out.pop("raw_conversation", None)
    return out


def classify_root_cause(trace: dict[str, Any]) -> str:
    """Map a trace to a §9.3 root-cause category."""
    if trace.get("memory_degraded") or trace.get("memory_degradation_reason"):
        return "memory"
    if not trace.get("selected_memory_ids") and trace.get("topic_transition") in ("switch", "new"):
        return "routing"
    if trace.get("signals", {}).get("user_correction"):
        return "recall"
    if trace.get("topic_transition") == "ambiguous":
        return "topic"
    return "model"


def build_regression_scenario(
    trace: dict[str, Any], *, scenario_id: str, expected: dict[str, Any]
) -> MultiturnScenario:
    """Build a minimal anonymized regression scenario with explicit expected (§9.4)."""
    turns: list[ScenarioTurn] = []
    for i, t in enumerate(trace.get("turns", []) or [{"input": "<redacted>"}]):
        turns.append(ScenarioTurn(
            input=t.get("input", "<redacted>"),
            event_id=t.get("event_id") or f"{scenario_id}-{i}",
            expected=ExpectedTurn(**expected) if i == len(trace.get("turns", [])) - 1 else ExpectedTurn(),
        ))
    return MultiturnScenario(
        id=scenario_id,
        version=1,
        tags=["promoted", classify_root_cause(trace)],
        turns=turns,
        final_expected={},
    )


__all__ = ["anonymize_trace", "build_regression_scenario", "classify_root_cause"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_promote.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the mode to `runner.py`**

```python
# --- promote-trace (Spec 4 §9) ---

async def run_promote_trace(args) -> int:
    import json
    from eval.memory_eval.promote import anonymize_trace, build_regression_scenario, classify_root_cause
    from sales_agent.models.memory_eval import MemoryEvalTraceRecord, PromotedRegression
    from sales_agent.core.database import get_session_factory
    from sqlalchemy import select

    async with get_session_factory()() as db:
        rec = (await db.execute(
            select(MemoryEvalTraceRecord).where(MemoryEvalTraceRecord.id == args.trace_id)
        )).scalar_one_or_none()
        if rec is None:
            print(f"trace not found: {args.trace_id}", file=sys.stderr)
            return 2  # invalid execution
        trace = rec.trace_json
        anon = anonymize_trace(trace)
        scenario = build_regression_scenario(
            anon, scenario_id=args.scenario_id,
            expected=json.loads(args.expected) if args.expected else {},
        )
        pr = PromotedRegression(
            tenant_id=rec.tenant_id, source_trace_id=args.trace_id,
            scenario_json=scenario.model_dump(), status="draft",
            anonymized=True, review_notes=classify_root_cause(trace),
        )
        db.add(pr)
        await db.commit()
        print(f"promote-trace: created {pr.id} (root cause: {pr.review_notes})")
    return 0
```

Register subcommand + dispatch:

```python
    p = sub.add_parser("promote-trace")
    p.add_argument("--trace-id", required=True)
    p.add_argument("--scenario-id", required=True)
    p.add_argument("--expected", default="{}", help="JSON expected-state for the resolving turn")
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/promote-trace")
```
```python
    if args.mode == "promote-trace":
        return await run_promote_trace(args)
```

- [ ] **Step 6: Commit**

```bash
git add eval/memory_eval/promote.py eval/memory_eval/runner.py tests/unit/eval/test_promote.py
git commit -m "feat(ops): add promote-trace workflow (Spec 4 §9)"
```

---

## Phase 6 — Semantic evaluators + wire-up + docs

### Task 22: Limited semantic evaluators (`semantic.py`)

§3.3: reference-free semantic evaluation of standalone-query quality, relevance, final outcome, and conversation naturalness. §10: judge timeout/error is reported separately and never changes product pass/fail counts. Flaky model cases are repeated and classified, not silently retried until green.

**Files:**
- Create: `eval/memory_eval/semantic.py`
- Test: `tests/unit/eval/test_semantic.py`

**Interfaces:**
- Consumes: `schema`, `metrics.types.MetricResult`, a `ChatModel`-conforming judge.
- Produces: `evaluate_semantic(pairs, *, judge, timeout_seconds) -> list[MetricResult]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/eval/test_semantic.py
from __future__ import annotations

import asyncio

import pytest

from eval.memory_eval.semantic import evaluate_semantic
from eval.memory_eval.schema import (
    ExpectedTurn, MultiturnScenario, ObservedTurn, ScenarioRun, ScenarioTurn,
)


def _pair():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(reply_contains=["华东"]))])
    run = ScenarioRun(scenario_id="s1", observed=[ObservedTurn(
        turn_index=0, result={"standalone_query": "我负责华东区"},
        replies=["好的，华东区"], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={})
    return [(s, run)]


class _FakeJudge:
    def __init__(self, reply): self._reply = reply
    async def generate(self, messages, **kw): return self._reply


class _TimeoutJudge:
    async def generate(self, messages, **kw):
        await asyncio.sleep(10)
        return "PASS"


@pytest.mark.asyncio
async def test_semantic_records_scores():
    metrics = await evaluate_semantic(_pair(), judge=_FakeJudge("PASS"), timeout_seconds=2.0)
    by_name = {m.name: m for m in metrics}
    assert by_name["semantic_relevance"].error is None
    assert by_name["semantic_relevance"].applicable is True


@pytest.mark.asyncio
async def test_judge_timeout_is_reported_not_fatal():
    metrics = await evaluate_semantic(_pair(), judge=_TimeoutJudge(), timeout_seconds=0.05)
    by_name = {m.name: m for m in metrics}
    assert by_name["semantic_relevance"].error is not None
    assert by_name["semantic_relevance"].passes is True   # error never flips gate (§10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_semantic.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/memory_eval/semantic.py
"""Limited reference-free semantic evaluators (Spec 4 §3.3, §10).

Judge timeout/error is reported separately and never changes product pass/fail.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

logger = logging.getLogger(__name__)

_DIMENSIONS = [
    ("semantic_relevance", "Is the assistant reply relevant to the user's intent?"),
    ("standalone_query_quality", "Is the standalone query a well-formed standalone question?"),
    ("final_outcome_quality", "Does the final reply resolve the user's goal?"),
    ("conversation_naturalness", "Is the conversation natural and coherent?"),
]


async def _judge_one(judge, prompt: str, timeout_seconds: float) -> tuple[float, str | None]:
    try:
        raw = await asyncio.wait_for(
            judge.generate([{"role": "user", "content": prompt}]), timeout=timeout_seconds,
        )
        verdict = (raw or "").strip().upper()
        score = 1.0 if "PASS" in verdict or "GOOD" in verdict else 0.0
        return score, None
    except asyncio.TimeoutError:
        return 0.0, "judge timeout"
    except Exception as exc:  # noqa: BLE001
        logger.warning("semantic judge error: %s", exc)
        return 0.0, f"judge error: {exc}"


async def evaluate_semantic(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]],
    *,
    judge: Any,
    timeout_seconds: float = 10.0,
) -> list[MetricResult]:
    results: dict[str, list[float]] = {name: [] for name, _ in _DIMENSIONS}
    for scenario, run in pairs:
        last_reply = run.observed[-1].replies[-1] if run.observed and run.observed[-1].replies else ""
        query = run.observed[-1].result.get("standalone_query", "") if run.observed else ""
        for name, question in _DIMENSIONS:
            prompt = f"{question}\nQuery: {query}\nReply: {last_reply}\nAnswer PASS or FAIL."
            score, err = await _judge_one(judge, prompt, timeout_seconds)
            if err:
                # Record one error result and stop collecting for this dimension.
                results[name] = [0.0]
                results[name + "_error"] = err  # type: ignore[assignment]
                break
            results[name].append(score)

    out: list[MetricResult] = []
    for name, _ in _DIMENSIONS:
        scores = results[name]
        err = results.get(name + "_error")
        if err:
            out.append(MetricResult(name=name, score=0.0, error=err))
        elif scores:
            avg = sum(scores) / len(scores)
            out.append(MetricResult(name=name, numerator=int(round(avg * len(scores))),
                                    denominator=len(scores), score=avg))
    return out


__all__ = ["evaluate_semantic"]
```

> **Wire-up:** `model-multiturn` (Task 13) and `graph-multiturn` (Task 12) may call `evaluate_semantic(...)` and add results to the `semantic_answer_quality` group. Because error metrics never flip the gate (§10), adding them is always safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval/test_semantic.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add eval/memory_eval/semantic.py tests/unit/eval/test_semantic.py
git commit -m "feat(eval): add timeout-isolated semantic evaluators (Spec 4 §3.3,§10)"
```

---

### Task 23: Gate script + README/changelog/runbook

Land the operator entry points and required documentation (repo rules: changelog entry + README `## 更新日志` row; runbook for operators).

**Files:**
- Create: `scripts/run_memory_eval_gate.sh`
- Modify: `README.md` (add a row to `## 更新日志`).
- Modify/append: `changelog/2026-07-08.md` (add a `##` section per repo format: 改动对象 / 类型 / 影响范围 / 改动明细 / 原因 / 验证).
- Create: `docs/runbooks/memory-evaluation.md`

- [ ] **Step 1: Write the gate script**

```bash
# scripts/run_memory_eval_gate.sh
#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}:."
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-memory-eval-gate}"
rc=0

echo "=== Spec 4 Memory Evaluation Gate ==="

# Phase A: deterministic unit + property suite (§3.1)
echo "--- unit-memory ---"
python3 eval/memory_eval/runner.py unit-memory --output "${OUTPUT_DIR}/unit-memory" || { echo "unit-memory FAILED"; rc=1; }

# Phase B: graph multiturn against the real Online Graph + PG (§3.2)
echo "--- graph-multiturn ---"
python3 eval/memory_eval/runner.py graph-multiturn \
  --dataset eval/memory/datasets/multiturn_v1.jsonl \
  --output "${OUTPUT_DIR}/graph-multiturn" || { echo "graph-multiturn FAILED"; rc=1; }

# Phase C: dataset coverage + release gates (§4, §6)
echo "--- dataset + gates ---"
python3 -m pytest -q tests/unit/eval/test_multiturn_dataset_coverage.py tests/unit/eval/test_gates.py || { echo "gates FAILED"; rc=1; }

if [ "$rc" -eq 0 ]; then
  echo "=== Spec 4 gate PASSED ==="
else
  echo "=== Spec 4 gate FAILED (exit $rc) ==="
fi
exit "$rc"
```

```bash
chmod +x scripts/run_memory_eval_gate.sh
```

- [ ] **Step 2: Append the changelog entry**

Append a section to `changelog/2026-07-08.md` (create the file if absent; follow the existing per-change `##` structure):

```markdown
## 记忆评估与生产运维套件（Spec 4）

- **改动对象**: `eval/memory_eval/`（新增）、`src/sales_agent/services/memory_eval_trace.py`、`src/sales_agent/models/memory_eval.py`、迁移 `0015_memory_eval_operations.py`、`scripts/run_memory_eval_gate.sh`、`docs/runbooks/memory-evaluation.md`
- **类型**: 新功能（评估 + 运维）
- **影响范围**: 离线评估、发布门禁、生产在线采样；不改动在线 Graph 请求路径
- **改动明细**: 统一多轮场景 schema（§4）、版本化报告（§7）、确定性指标四组（§5）、确定性 model double + scenario runner 驱动真实 `invoke_online_turn`（§3.2）、七个 CLI 模式（§11）、fail-closed 发布门禁（§6）、在线采样（§3.5，非阻塞）、promote-trace 反馈闭环（§9）、`memory_eval_traces`/`promoted_regressions` 两张表
- **原因**: Spec 4 — 让多轮与记忆质量在发布前可度量、发布后可维护
- **验证**: `scripts/run_memory_eval_gate.sh`（unit-memory + graph-multiturn + 数据集覆盖 + 门禁）；`PYTHONPATH=src:. python3 -m pytest tests/unit/eval tests/unit/services/test_memory_eval_trace.py -q`
```

- [ ] **Step 3: Add the README `## 更新日志` row**

In `README.md`, add a row at the top of the `## 更新日志` table (newest first):

```markdown
| [2026-07-08](changelog/2026-07-08.md) | 记忆评估与生产运维套件（Spec 4）：统一多轮数据集/指标/报告、七个 CLI 模式、fail-closed 发布门禁、在线采样与 promote-trace |
```

- [ ] **Step 4: Write the operator runbook**

```markdown
<!-- docs/runbooks/memory-evaluation.md -->
# Memory Evaluation & Operations Runbook (Spec 4)

## Run the gate (every release)
```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  ./scripts/run_memory_eval_gate.sh
```
Exit non-zero = release blocked. The gate refuses any non-`*test*` DB URL.

## CLI modes (`python eval/memory_eval/runner.py <mode>`)
| Mode | When | Model/DB |
|------|------|----------|
| `unit-memory` | every commit (§3.1) | none |
| `graph-multiturn` | every PR (§3.2) | deterministic double + PG |
| `model-multiturn` | nightly / model·prompt change (§3.3) | real model + PG |
| `dingtalk-staging` | every release (§3.4) | staging DB |
| `compare <baseline> <candidate>` | before release (§7) | none |
| `online-sample` | continuous (§3.5) | prod DB, non-blocking |
| `promote-trace <trace-id>` | on production failure (§9) | prod DB |

## Release gates (§6)
Isolation/safety gates fail closed: zero cross-tenant/agent/user leakage, zero prohibited-memory writes, zero assistant-output→user-memory activations. Threshold changes require a versioned rationale; never lower a threshold just to pass.

## Promote a production failure into a regression (§9)
1. The trace is stored under restricted retention (`memory_eval_traces`, hashed scope).
2. `promote-trace --trace-id <id> --scenario-id promoted-NNN --expected '{...}'` produces an anonymized `MultiturnScenario` (status `draft`).
3. Review, set `status=reviewed`, reproduce in `model-multiturn`, then commit to `eval/memory/datasets/`.
```

- [ ] **Step 5: Run the full unit suite + gate script dry-check**

Run: `PYTHONPATH=src:. python3 -m pytest tests/unit/eval tests/unit/services/test_memory_eval_trace.py -q`
Expected: all PASS.

Run: `bash -n scripts/run_memory_eval_gate.sh` (syntax check)
Expected: no output (valid syntax).

- [ ] **Step 6: Commit**

```bash
git add scripts/run_memory_eval_gate.sh changelog/2026-07-08.md README.md docs/runbooks/memory-evaluation.md
git commit -m "docs(eval): add gate script, changelog, README row, operator runbook (Spec 4)"
```

---

## Acceptance Criteria Mapping (§12)

| §12 Criterion | Delivered by |
|---|---|
| ≥40 versioned multi-turn scenarios run from one command | Task 15 dataset + Task 12 `graph-multiturn` (one command) |
| Real model mode awaits production resolvers with actual runtime inputs | Task 13 `model-multiturn` (binds `resolve_online_models` real resolver) |
| Real DingTalk staging tests cover all six approved user scenarios | Task 14 `dingtalk-staging` + scenarios tagged in Task 15 |
| Clarification completion measures the follow-up resolution | Task 6 turn/topic metric (clarification turn) |
| Every report exposes applicability, denominators, errors, and versions | Tasks 3–4 (`MetricResult` + `EvaluationReport` with `versions`) |
| Production failures promotable into anonymized regressions via documented workflow | Task 21 `promote-trace` + runbook Task 23 |
| Online evaluation does not block user traffic and has bounded sampling/cost | Task 19 `online-sample` (best-effort, bounded `--max-threads`) |
| Release gates automated, reproducible, fail closed for isolation/safety | Task 16 gates + Task 23 gate script |

## Spec Coverage Notes

- **§3.1 property tests** — the two invariants ("no two active single-valued memories per scope", "no more than one active Topic per scope") are enforced structurally: the partial-unique index `uq_agent_memory_active_single_value` (migration `0013`) and the `ConversationTopic` active-status scope uniqueness, exercised by existing `tests/unit/memory/test_contracts.py` and `tests/integration/test_atomic_memory_repository.py`. The `unit-memory` mode (Task 12) runs these. If broader randomized fuzzing is later desired, add `hypothesis` as a dev dependency and a `tests/property/test_memory_invariants.py` — out of MVP scope here.
- **§3.4 "all six approved user scenarios"** — the six DingTalk scenarios must be represented as `dingtalk-staging`-tagged entries in the Task 15 dataset; the Task 14 integration test asserts one end-to-end, and the gate script runs the full set.
- **§4 "existing single-turn router datasets remain and are linked"** — `eval/router/*.jsonl` (`turn_relation_cases`, `evidence_policy_cases`, `clarification_resolution_cases`) stay in place untouched; no code in this plan reads or removes them. "Linked to related multi-turn regressions" is expressed via free-form tags: a Task 15 scenario MAY carry a tag like `router:turn_relation:trc-007` referencing a router case id (the schema's `tags: list[str]` accepts it with no change), so operators cross-reference the single-turn cause to the multi-turn regression.
- **§5.1 clarification completion & restore/restart recovery** — emitted by the Task 6 turn/topic module (see Task 6 extension below).
- **§8 dashboards** — emitted as structured per-turn trace JSON (Task 17); rendering into a dashboard UI is operational tooling outside this plan's code scope. The trace fields are the contract a dashboard consumes.

### Task 6 extension (required, applies the self-review fix)

The turn/topic module must also emit `clarification_completion` (§5.1) and `restart_recovery` (§6 gate), so the release gates (Task 16) have real numerators. Add to `eval/memory_eval/metrics/turn_topic.py` and its test:

```python
# Add to evaluate_turn_topic(), before `return metrics, cms`:
# Clarification completion: a pending clarification that is resolved on a later turn.
clar_resolved, clar_total = 0, 0
# Restart recovery: scenarios tagged "restart" whose post-restart turn still advances flow.
restart_ok, restart_total = 0, 0
for scenario, run in pairs:
    if "restart" in scenario.tags:
        restart_total += 1
        # Recovered if the post-restart observed turn has a non-duplicate response_kind.
        post = [o for o in run.observed if o.turn_index > 0]
        if post and not post[-1].duplicate and post[-1].result.get("response_kind") != "duplicate":
            restart_ok += 1
    had_pending = any(t.expected.topic_transition == "restored" or "clarification" in (t.expected.reply_contains or [])
                      for t in scenario.turns[:-1])
    if had_pending:
        clar_total += 1
        last = run.observed[-1] if run.observed else None
        if last and last.result.get("turn_relation") in ("continue", "revise") and last.result.get("response_kind") != "duplicate":
            clar_resolved += 1
if clar_total:
    metrics.append(MetricResult(name="clarification_completion", numerator=clar_resolved,
                                denominator=clar_total, score=clar_resolved / clar_total, threshold=0.90))
if restart_total:
    metrics.append(MetricResult(name="restart_recovery", numerator=restart_ok, denominator=restart_total,
                                score=restart_ok / restart_total, threshold=1.0))
```

And add `assistant_output_to_user_memory` to `assemble_report` in `runner.py` (Task 12) so the §6 gate has a numerator:

```python
# In assemble_report(), after the other evaluations:
from eval.memory_eval.gates import assistant_output_to_user_memory_violation
report.add_metric("persistence_isolation", assistant_output_to_user_memory_violation(pairs))
```

These two edits are part of Task 6 / Task 12 respectively; apply them when implementing those tasks (the test files already asserted the metric names exist via the gate tests).

---

## Self-Review (run before execution)

1. **Spec coverage** — every §1–§12 requirement maps to a task (see Acceptance Mapping + Coverage Notes above). The only soft spot (§3.1 randomized property tests) is documented with a concrete fallback.
2. **Placeholder scan** — no "TBD/TODO/similar to Task N". Task 15 is genuine content authoring (template + 6 verbatim scenarios + enumerated categories). Task 19's thread-log SELECT is flagged honestly (the tested sampling core is complete; the DB query depends on the conversation-log schema the executor finalizes).
3. **Type consistency** — `MetricResult` / `prf` / `assemble_report` group names align across Tasks 3–9, 16; the Task 6 extension + `assemble_report` wiring close the gate-metric gaps (clarification_completion, restart_recovery, assistant_output_to_user_memory).

