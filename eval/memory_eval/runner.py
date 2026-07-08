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
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun
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
    # TODO(Task 16): add assistant_output_to_user_memory_violation(pairs) to
    # persistence_isolation once gates.py exists. Do not fail the build on its
    # absence here.
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
    out = write_report(report, args.output)
    print(
        f"graph-multiturn: {'PASS' if report.thresholds_met else 'FAIL'}  "
        f"scenarios={len(pairs)} report={out}"
    )
    return 0 if report.thresholds_met else 1


def main(argv: Optional[list[str]] = None) -> int:
    """CLI dispatcher for the seven documented modes (Spec 4 §11).

    Modes ``model-multiturn``, ``dingtalk-staging``, ``compare``,
    ``online-sample`` and ``promote-trace`` are registered by later tasks.
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

    args = parser.parse_args(argv)
    if args.mode == "unit-memory":
        return run_unit_memory(args)
    if args.mode == "graph-multiturn":
        import asyncio

        return asyncio.run(run_graph_multiturn(args))
    # argparse already rejects unknown subcommands with SystemExit(2); this is a
    # defensive fallback for completeness.
    parser.error(f"unknown mode: {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
