"""Memory-evaluation CLI dispatcher with the seven documented modes (Spec 4 §11).

This module lands the dispatcher plus the first two modes:

* ``unit-memory`` (§3.1) runs the deterministic unit/property suite via a
  subprocess ``pytest`` invocation and aggregates pass/fail into a report.
* ``graph-multiturn`` (§3.2) drives the real Online Graph with the
  deterministic model/embedding doubles against PostgreSQL checkpoints and the
  memory tables, aggregating the four metric modules into one report.

Later tasks (13, 14, 19, 20, 21) register the remaining modes on the same
dispatcher. Each mode writes JSON plus Markdown and exits non-zero only for its
own quality gates (``1``) or invalid execution (``2``).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from eval.memory_eval.dataset import DatasetValidationError, load_scenarios, validate_dataset
from eval.memory_eval.gates import assistant_output_to_user_memory_violation, check_gates
from eval.memory_eval.metrics.conversation import evaluate_conversation
from eval.memory_eval.metrics.memory_lifecycle import evaluate_memory_lifecycle
from eval.memory_eval.metrics.recall_profile import evaluate_recall_profile
from eval.memory_eval.metrics.turn_topic import evaluate_turn_topic
from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.model_double import (
    DeterministicEmbeddingDouble,
    ScriptedModelDouble,
    TurnScript,
)
from eval.memory_eval.report import EvaluationReport, write_report
from eval.memory_eval.schema import MultiturnScenario, ObservedTurn, ScenarioRun
from eval.memory_eval.scenario_runner import ScenarioRunner
from eval.memory_eval.versions import collect_version_bundle

DEFAULT_DATASET = "eval/memory/datasets/multiturn_v1.jsonl"
DEFAULT_OUTPUT = "/tmp/sales-agent-memory-eval"

# Deterministic unit/property suite run by ``unit-memory`` (Spec 4 §3.1).
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
    Missing expectation fields fall back to neutral values so the double always
    has something to emit.
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


def assemble_report(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]], versions: Any
) -> EvaluationReport:
    """Aggregate the four metric modules over (scenario, run) pairs.

    Metric placement into reporting groups (Spec 4 §7):

    * turn & topic            -> ``deterministic_state_safety``
    * memory lifecycle        -> ``persistence_isolation``
    * recall & profile        -> ``persistence_isolation``
    * conversation/trajectory -> ``trajectory``
    """
    report = EvaluationReport(
        versions=versions,
        total_scenarios=len(pairs),
        total_turns=sum(len(s.turns) for s, _ in pairs),
    )
    # Assistant-output-to-user-memory violation (§6): counts assistant text
    # wrongly activated as a user-scoped memory. Added before the metric modules
    # so it lands in the persistence_isolation group alongside the other safety
    # gates (prohibited_memory_write_count / cross_scope_leakage).
    report.add_metric("persistence_isolation", assistant_output_to_user_memory_violation(pairs))
    for group, (metrics, cms) in [
        ("deterministic_state_safety", evaluate_turn_topic(pairs)),
        ("persistence_isolation", evaluate_memory_lifecycle(pairs)),
        ("persistence_isolation", evaluate_recall_profile(pairs)),
        ("trajectory", evaluate_conversation(pairs)),
    ]:
        for metric in metrics:
            report.add_metric(group, metric)
        for name, cm in cms.items():
            report.add_confusion(name, cm)
    return report


def _apply_release_gates(report: EvaluationReport) -> None:
    """Enforce the §6 release gate list (fail-closed) before writing a report.

    The per-metric ``thresholds_met`` flag already flips on each metric's own
    ``pass_if`` threshold. This adds the authoritative §6 gate list on top:
    every release gate is checked, and an isolation/safety gate that was never
    evaluated counts as a FAILURE. Gate failures are merged into
    ``report.failures`` so the exit code (and written report) reflects both
    signals. Call this between :func:`assemble_report` and :func:`write_report`
    in every report-emitting mode.
    """
    gr = check_gates(report)
    if not gr.passed:
        report.thresholds_met = False
        report.failures.extend(gr.failed_gates)


def _load_or_fail(dataset: str) -> list[MultiturnScenario]:
    """Load and validate the dataset, exiting on invalid execution (§11)."""
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
    """§3.1: run the deterministic unit/property suite and aggregate to a report."""
    report = EvaluationReport(versions=collect_version_bundle(dataset_version="unit"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *UNIT_TEST_PATHS],
        capture_output=True,
        text=True,
    )
    passed, failed = _parse_pytest_summary(proc.stdout + proc.stderr)
    total = passed + failed
    report.add_metric(
        "deterministic_state_safety",
        MetricResult(
            name="unit_property_suite",
            numerator=passed,
            denominator=total,
            score=(passed / total) if total else 0.0,
            threshold=1.0,
        ),
    )
    out = write_report(report, args.output)
    print(f"unit-memory: {'PASS' if report.thresholds_met else 'FAIL'}  report={out}")
    return 0 if report.thresholds_met else 1


def _parse_pytest_summary(text: str) -> tuple[int, int]:
    """Parse the pytest summary line for passed/failed counts."""
    m = re.search(r"(\d+) passed", text)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", text)
    failed = int(m.group(1)) if m else 0
    return passed, failed


def _make_restart_runtime():
    """Build a restart callback that closes and re-initializes the online graph.

    Both the checkpointer and the app session factory resolve the DB URL from
    the shared ``get_settings()`` singleton, which ``run_graph_multiturn`` has
    already re-pointed at the test DB, so no per-module patching is needed.
    """

    async def _do() -> None:
        from sales_agent.services.online_conversation import (
            close_online_runtime,
            initialize_online_runtime,
        )

        await close_online_runtime()
        await initialize_online_runtime()

    return _do


async def _bind_test_database(test_db: str) -> dict:
    """Rebind the app session factory AND the online checkpointer to ``test_db``.

    Returns a snapshot dict containing the original values of every global
    that this function mutates, so the caller can restore them in a ``finally``
    block and avoid poisoning the process for any later code::

        snapshot = await _bind_test_database(test_url)
        try:
            ...
        finally:
            await _restore_database(snapshot)

    The checkpointer (``initialize_production_checkpointer``), the app session
    factory (``get_session_factory``), and the graph nodes all read the DB URL
    from the shared ``get_settings()`` singleton. Mutating that singleton once
    and resetting any cached engine/factory (built from the previous dev URL)
    makes every consumer hit the test DB consistently — fixing the hollow-pass
    bug where only the checkpointer was patched while ``get_session_factory``
    still bound to the unreachable ``localhost:5432`` dev DB.

    The §11 "refuse a non-test DB" contract is enforced by the caller's guard.
    """
    import sales_agent.core.database as database
    from sales_agent.core.config import get_settings

    settings = get_settings()

    # Snapshot originals before mutating.
    snapshot = {
        "database_url": settings.database.url,
        "long_term_memory_enabled": settings.long_term_memory.enabled,
        "engine": database._engine,
        "session_factory": database._session_factory,
    }

    settings.database.url = test_db
    # The memory_command routing gate reads this flag from the turn input,
    # which prepare_online_turn seeds from settings.long_term_memory.enabled.
    settings.long_term_memory.enabled = True

    # Drop any engine/factory cached against the dev URL so they are rebuilt
    # against the test DB on next access.
    if database._engine is not None:
        await database._engine.dispose()
    database._engine = None
    database._session_factory = None

    # Ensure every ORM model is registered on Base.metadata, then create the
    # application tables on the test DB (it ships with checkpoint tables only;
    # checkpoint tables are created separately by AsyncPostgresSaver.setup()).
    import sales_agent.models  # noqa: F401
    sales_agent.models._import_dingtalk_models()
    from sales_agent.core.database import Base, get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    return snapshot


async def _restore_database(snapshot: dict) -> None:
    """Restore global DB settings and cached engine/factory from a snapshot.

    Disposes the test engine (if one was created), resets the settings
    singleton back to the original URL and memory flag, and restores the
    cached ``_engine`` / ``_session_factory`` module globals.
    """
    import sales_agent.core.database as database
    from sales_agent.core.config import get_settings

    # Dispose the test engine that _bind_test_database created.
    if database._engine is not None:
        await database._engine.dispose()

    settings = get_settings()
    settings.database.url = snapshot["database_url"]
    settings.long_term_memory.enabled = snapshot["long_term_memory_enabled"]
    database._engine = snapshot["engine"]
    database._session_factory = snapshot["session_factory"]


async def _seed_eval_fixtures(factory, tenant_id: str, agent_id: str) -> None:
    """Idempotently seed a tenant + active default agent for graph-multiturn.

    Chat + embedding models are injected as deterministic doubles, so
    ``resolve_online_models`` short-circuits and no tenant model config is
    required. ``resolve_tenant_agent_id`` only needs an Agent row owned by the
    tenant; the Tenant row keeps the schema coherent for any node that joins it.
    """
    from sqlalchemy import select
    from sales_agent.models.agent import Agent
    from sales_agent.models.tenant import Tenant

    async with factory()() as db:
        if (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none() is None:
            db.add(
                Tenant(
                    id=tenant_id,
                    name="Memory Eval Tenant",
                    status="active",
                    config_json="{}",
                )
            )
        if (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none() is None:
            db.add(
                Agent(
                    id=agent_id,
                    tenant_id=tenant_id,
                    name="Memory Eval Agent",
                    status="active",
                    feature_flags_json="{}",
                    is_tenant_default=True,
                )
            )
        await db.commit()


async def run_graph_multiturn(args) -> int:
    """§3.2: drive the real Online Graph with deterministic doubles + PostgreSQL."""
    test_db = os.environ.get("TEST_DATABASE_URL", "")
    if "test" not in test_db:
        print("graph-multiturn requires TEST_DATABASE_URL containing 'test'", file=sys.stderr)
        return 2  # invalid execution (§11)

    scenarios = _load_or_fail(args.dataset)
    scripts = build_scripts_from_scenarios(scenarios)
    double = ScriptedModelDouble(scripts)
    embedding_double = DeterministicEmbeddingDouble()

    # Rebind the whole app stack (session factory + checkpointer) to the test DB,
    # create app tables, and seed the tenant/agent the graph resolves against.
    snapshot = await _bind_test_database(test_db)
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.online_conversation import (
        close_online_runtime,
        initialize_online_runtime,
    )

    await _seed_eval_fixtures(get_session_factory, args.tenant_id, args.agent_id)
    restart_runtime = _make_restart_runtime()

    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    await close_online_runtime()
    try:
        await initialize_online_runtime()
        # A per-invocation token keeps the Online Graph's duplicate-event
        # detection honest: the checkpointer persists ``last_event_id`` keyed by
        # thread_id, and thread_id is derived from session_user_id. Without a
        # fresh token, a re-run against the persistent test DB would reuse the
        # same thread_id and the graph would classify every event as a
        # duplicate of the prior run — masking real execution (hollow pass).
        # The token is shared across all turns/scenarios of THIS run so
        # multi-turn state still accumulates within a scenario.
        run_token = uuid.uuid4().hex[:8]
        for s in scenarios:
            # Fresh session per scenario (pattern from tests/conftest.py db_session).
            async with get_session_factory()() as db:
                ctx = {
                    "db": db,
                    "tenant_id": args.tenant_id,
                    "agent_id": args.agent_id,
                    "user_id": f"{s.id}-user-{run_token}",
                    "session_user_id": f"{s.id}-session-{run_token}",
                    "channel": "eval",
                    "conversation_id": f"{s.id}-conv-{run_token}",
                    # Both models are injected so resolve_online_models returns
                    # early and never falls back to TenantResolver (which would
                    # need tenant model config / a live model provider).
                    "chat_model": double,
                    "embedding_model": embedding_double,
                }
                # ScenarioRunner advances the scripted double per turn itself
                # (set_turn(scenario.id, i)), so no pre-loop seeding is needed.
                runner = ScenarioRunner(ctx=ctx, restart_runtime=restart_runtime)
                run = await runner.run(s)
                pairs.append((s, run))
    finally:
        await close_online_runtime()
        await _restore_database(snapshot)

    report = assemble_report(pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem))
    _apply_release_gates(report)
    out = write_report(report, args.output)
    print(
        f"graph-multiturn: {'PASS' if report.thresholds_met else 'FAIL'}  "
        f"scenarios={len(pairs)} report={out}"
    )
    return 0 if report.thresholds_met else 1


# --- model-multiturn (Spec 4 §3.3) ---

# Ambiguous/boundary scenarios are repeated this many times and their
# cross-repetition consistency is reported (§3.3).
REPETITIONS_FOR_AMBIGUOUS = 3


def classify_repetitions(observed_seqs: list[list[str]]) -> str:
    """Label a 3x repetition set as consistent/flaky (Spec 4 §3.3).

    ``observed_seqs`` is the list of per-run observed turn-relation sequences
    (one list of labels per repetition). Returns ``"na"`` for an empty input,
    ``"consistent"`` when every repetition agrees with the first, and
    ``"flaky"`` otherwise.
    """
    if not observed_seqs:
        return "na"
    first = observed_seqs[0]
    if all(seq == first for seq in observed_seqs[1:]):
        return "consistent"
    return "flaky"


def _is_ambiguous_boundary(scenario: MultiturnScenario) -> bool:
    """Whether a scenario needs 3x repetitions per §3.3.

    True when any turn declares an ``ambiguous`` turn relation or the scenario
    carries a ``boundary`` tag — the two cases the spec calls out as needing
    repeated runs to characterize consistency.
    """
    return any(t.expected.turn_relation == "ambiguous" for t in scenario.turns) or \
        "boundary" in scenario.tags


async def run_model_multiturn(args) -> int:
    """§3.3: run the real production model against the versioned dataset.

    Uses the configured tenant model provider (no doubles). Every scenario
    produces deterministic metrics; ambiguous/boundary scenarios are repeated
    :data:`REPETITIONS_FOR_AMBIGUOUS` times and their cross-repetition
    consistency is reported. This mode awaits production resolvers with their
    actual runtime inputs (acceptance criterion §12.2), so it is never
    exercised by the unit suite — only its credentials gate and the pure
    ``classify_repetitions`` helper are unit-tested.
    """
    if args.no_credentials or not os.environ.get("MODEL_API_KEY"):
        print(
            "model-multiturn requires real model credentials (MODEL_API_KEY)",
            file=sys.stderr,
        )
        return 2  # invalid execution (§11), not a quality failure

    scenarios = _load_or_fail(args.dataset)
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.online_conversation import resolve_online_models

    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    consistency: dict[str, str] = {}
    for s in scenarios:
        repetitions = REPETITIONS_FOR_AMBIGUOUS if _is_ambiguous_boundary(s) else 1
        seqs: list[list[str]] = []
        run: ScenarioRun | None = None
        for _ in range(repetitions):
            async with get_session_factory()() as db:
                # Real resolver — no injected doubles — so the tenant's
                # configured provider supplies the actual chat/embedding model.
                chat_model, embedding_model = await resolve_online_models(
                    db=db, tenant_id=args.tenant_id,
                )
                ctx = {
                    "db": db,
                    "tenant_id": args.tenant_id,
                    "agent_id": args.agent_id,
                    "user_id": f"{s.id}-user",
                    "session_user_id": f"{s.id}-session",
                    "channel": "eval",
                    "conversation_id": f"{s.id}-conv",
                    "chat_model": chat_model,
                    "embedding_model": embedding_model,
                }
                runner = ScenarioRunner(ctx=ctx)
                run = await runner.run(s)
                seqs.append([o.result.get("turn_relation", "na") for o in run.observed])
        if run is not None:
            pairs.append((s, run))
        if repetitions > 1:
            consistency[s.id] = classify_repetitions(seqs)

    report = assemble_report(
        pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem)
    )
    report.consistency = consistency
    _apply_release_gates(report)
    out = write_report(report, args.output)
    print(
        f"model-multiturn: {'PASS' if report.thresholds_met else 'FAIL'}  "
        f"scenarios={len(pairs)} report={out}"
    )
    return 0 if report.thresholds_met else 1


# --- dingtalk-staging (Spec 4 §3.4) ---


async def run_dingtalk_staging(args) -> int:
    """§3.4: normalized HTTP/stream events through ``handle_dingtalk_event``.

    Drives the real DingTalk processor against the test DB with staging users,
    deterministic model doubles, and a :class:`PublicReplyCapture` that keeps
    ONLY public outbound delivery (dropping ``[internal]``/``[audit]``/
    ``[memory-internal]`` prefixed messages). Restart + worker-switch steps
    between turns are honored via the runtime re-init callback. Agent
    resolution runs naturally against a seeded tenant-default agent (no
    monkeypatching).

    Refuses a non-test DB (rc=2, §11). Returns 0 when all quality thresholds
    are met, 1 otherwise.
    """
    import dataclasses

    test_db = os.environ.get("TEST_DATABASE_URL", "")
    if "test" not in test_db:
        print("dingtalk-staging requires TEST_DATABASE_URL containing 'test'", file=sys.stderr)
        return 2  # invalid execution (§11)

    scenarios = _load_or_fail(args.dataset)
    scripts = build_scripts_from_scenarios(scenarios)
    double = ScriptedModelDouble(scripts)
    embedding_double = DeterministicEmbeddingDouble()

    # Rebind the whole app stack (session factory + checkpointer) to the test DB,
    # create app tables, and seed the tenant/agent the processor resolves against.
    snapshot = await _bind_test_database(test_db)
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import get_session_factory
    from sales_agent.integrations.dingtalk.config import DingTalkConfig
    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event
    from sales_agent.services.online_conversation import (
        close_online_runtime,
        initialize_online_runtime,
    )
    from eval.memory_eval.dingtalk_capture import PublicReplyCapture
    from types import SimpleNamespace
    from unittest.mock import patch

    settings = get_settings()
    await _seed_eval_fixtures(get_session_factory, args.tenant_id, args.agent_id)
    restart_runtime = _make_restart_runtime()
    config = DingTalkConfig()

    pairs: list[tuple[MultiturnScenario, ScenarioRun]] = []
    await close_online_runtime()
    try:
        await initialize_online_runtime()
        # Per-invocation token keeps the Online Graph's duplicate-event detection
        # honest: thread_id is derived from session_user_id (the DingTalk
        # sender_id), so a fresh token yields fresh thread_ids and a re-run
        # against the persistent test DB is not classified as a duplicate.
        run_token = uuid.uuid4().hex[:8]

        # Inject deterministic model doubles at the resolution boundary.
        # ``handle_dingtalk_event`` calls ``invoke_online_turn`` internally
        # without exposing model params, so the doubles must be injected where
        # ``resolve_online_models`` is called (mirroring graph-multiturn's
        # injection of the same doubles via the ctx dict).
        async def _resolve_with_doubles(*, db, tenant_id, chat_model=None, embedding_model=None):
            return double, embedding_double

        with patch(
            "sales_agent.services.online_conversation.resolve_online_models",
            _resolve_with_doubles,
        ):
            for s in scenarios:
                observed: list[ObservedTurn] = []
                # Fresh session per scenario (same pattern as graph-multiturn).
                async with get_session_factory()() as db:
                    runtime = SimpleNamespace(tenant_id=args.tenant_id)
                    prev_worker_id: str | None = None
                    for i, turn in enumerate(s.turns):
                        # Advance the scripted double to THIS turn's script.
                        double.set_turn(s.id, i)

                        # Restart step (§3.4): close + re-init the online
                        # runtime between turns to exercise cold-start state
                        # recovery from the checkpointer.
                        if turn.restart_before:
                            await restart_runtime()

                        # Worker-switch (§3.4): a changed worker_id means the
                        # event arrived on a different worker process, so the
                        # runtime must cold-start from the checkpointer — the
                        # same recovery path as restart_before. Agent resolution
                        # runs naturally against the seeded tenant-default agent
                        # (no monkeypatch).
                        if turn.worker_id and turn.worker_id != prev_worker_id:
                            await restart_runtime()
                        prev_worker_id = turn.worker_id

                        cap = PublicReplyCapture()
                        result = await handle_dingtalk_event(
                            db,
                            config,
                            settings,
                            runtime,
                            event_id=turn.event_id or f"{s.id}-{i}",
                            corp_id="staging-corp",
                            sender_id=f"{s.id}-sender-{run_token}",
                            sender_name="staging",
                            message_type="text",
                            text=turn.input,
                            dingtalk_conversation_id=f"{s.id}-dt-{run_token}",
                            reply_fn=cap.reply,
                        )
                        # Query normalized keys for the active memories written
                        # this turn (mirrors _default_capture in
                        # scenario_runner.py — memory_ids are UUIDs, but
                        # active_memory_keys must be the human-readable
                        # normalized_key values the metrics compare against).
                        active_keys: list[str] = []
                        if result.memory_ids:
                            from sqlalchemy import select
                            from sales_agent.models.atomic_memory import AtomicMemory

                            rows = (
                                await db.execute(
                                    select(AtomicMemory.normalized_key).where(
                                        AtomicMemory.id.in_(result.memory_ids),
                                        AtomicMemory.status == "active",
                                    )
                                )
                            ).scalars().all()
                            active_keys = list(rows)
                        observed.append(
                            ObservedTurn(
                                turn_index=i,
                                result=dataclasses.asdict(result),
                                replies=cap.public_replies,
                                active_topic_ids=[],
                                closed_topic_ids=[],
                                active_memory_keys=active_keys,
                                selected_memory_ids=result.selected_memory_ids or [],
                                duplicate=result.response_kind == "duplicate",
                            )
                        )
                        # Persist this turn's writes so the next turn (and the
                        # checkpointer) see consistent state.
                        await db.commit()
                pairs.append(
                    (s, ScenarioRun(scenario_id=s.id, observed=observed, final_state={}))
                )
    finally:
        await close_online_runtime()
        await _restore_database(snapshot)

    report = assemble_report(
        pairs, collect_version_bundle(dataset_version=Path(args.dataset).stem)
    )
    _apply_release_gates(report)
    out = write_report(report, args.output)
    print(
        f"dingtalk-staging: {'PASS' if report.thresholds_met else 'FAIL'}  "
        f"scenarios={len(pairs)} report={out}"
    )
    return 0 if report.thresholds_met else 1


def main(argv: Optional[list[str]] = None) -> int:
    """CLI dispatcher for the seven documented modes (Spec 4 §11).

    Async modes (``graph-multiturn``, ``model-multiturn``,
    ``dingtalk-staging``, and the later ``online-sample`` / ``promote-trace``)
    all route through one ``asyncio.run(_async_dispatch(args))`` so later tasks
    only add a branch to ``_async_dispatch``. ``unit-memory`` and ``compare``
    stay synchronous.
    """
    parser = argparse.ArgumentParser(
        prog="memory-eval", description="Spec 4 memory evaluation"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("unit-memory", help="run the deterministic unit/property suite (§3.1)")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/unit-memory")

    p = sub.add_parser("graph-multiturn", help="drive the real Online Graph (§3.2)")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/graph-multiturn")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")

    p = sub.add_parser(
        "model-multiturn",
        help="drive the real production model against the versioned dataset (§3.3)",
    )
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/model-multiturn")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")
    p.add_argument("--no-credentials", action="store_true",
                   help="assert credentials are absent (forces rc=2, invalid execution)")

    p = sub.add_parser(
        "dingtalk-staging",
        help="drive the real DingTalk processor with staging users (§3.4)",
    )
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output", default=f"{DEFAULT_OUTPUT}/dingtalk-staging")
    p.add_argument("--tenant-id", default="eval-tenant")
    p.add_argument("--agent-id", default="eval-agent")

    args = parser.parse_args(argv)

    # All async modes share one event loop / dispatch entry point.
    ASYNC_MODES = ("graph-multiturn", "model-multiturn",
                   "dingtalk-staging", "online-sample", "promote-trace")
    if args.mode in ASYNC_MODES:
        import asyncio

        return asyncio.run(_async_dispatch(args))
    if args.mode == "unit-memory":
        return run_unit_memory(args)
    # argparse already rejects unknown subcommands with SystemExit(2); this is a
    # defensive fallback for completeness.
    parser.error(f"unknown mode: {args.mode}")
    return 2


async def _async_dispatch(args) -> int:
    """Route async modes to their handlers (Spec 4 §11).

    Later tasks register ``online-sample`` / ``promote-trace`` here. Keeping
    a single dispatch entry point means ``main`` only needs one
    ``asyncio.run`` call regardless of how many async modes exist.
    """
    if args.mode == "graph-multiturn":
        return await run_graph_multiturn(args)
    if args.mode == "model-multiturn":
        return await run_model_multiturn(args)
    if args.mode == "dingtalk-staging":
        return await run_dingtalk_staging(args)
    # Later async modes (online-sample / promote-trace) are registered by
    # Tasks 19/21. Reaching here means the subparser accepted a mode that has
    # no handler yet — treat as invalid execution.
    raise SystemExit(f"unhandled async mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
