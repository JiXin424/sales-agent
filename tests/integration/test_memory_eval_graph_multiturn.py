"""Integration: graph-multiturn mode drives the real Online Graph (Spec 4 §3.2).

This is a stretch verification for Task 12. It drives the real Online Graph with
the deterministic model double against PostgreSQL checkpoints. It requires a
reachable test Postgres and a seeded tenant/agent; when the environment is not
wired for that it reports the gap rather than masking it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_graph_multiturn_writes_report(tmp_path):
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        pytest.skip("TEST_DATABASE_URL not set to a test database")

    # Dataset mixes an explicit-remember scenario (routes to memory_command,
    # bypassing tenant_resolve) with a chat-path scenario (routes to the chat
    # node → resolve_tenant → TenantResolver.resolve → check_tenant_match).
    # The chat-path scenario is the regression guard for the standalone-CLI
    # tenant-mismatch defect: without binding the tenant runtime to the eval
    # tenant, resolve_tenant raises TenantMismatchError.
    ds = tmp_path / "ds.jsonl"
    ds.write_text(
        json.dumps(
            {
                "id": "gm-001",
                "version": 1,
                "tags": ["explicit", "remember"],
                "turns": [
                    {
                        "input": "记住我负责华东区",
                        "event_id": "gm-001-1",
                        "expected": {
                            "turn_relation": "new",
                            "memory_operation": "remember",
                            "active_memory_keys": ["sales_region"],
                            "reply_contains": ["华东"],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "id": "gm-chat-001",
                "version": 1,
                "tags": ["chat"],
                "turns": [
                    {
                        "input": "帮我看看福多多产品怎么讲",
                        "event_id": "gm-chat-001-1",
                        "expected": {
                            "turn_relation": "new",
                            "reply_contains": ["福多多"],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    from eval.memory_eval.runner import run_graph_multiturn

    rc = await run_graph_multiturn(
        SimpleNamespace(
            dataset=str(ds),
            output=str(tmp_path / "out"),
            tenant_id="eval-tenant",
            agent_id="eval-agent",
        )
    )
    assert (tmp_path / "out" / "report.json").exists()
    # Exit code 0 or 1 (quality), never 2 (invalid execution) on a valid setup.
    assert rc in (0, 1)

    # Non-hollow execution: the report must show the graph genuinely ran and
    # the explicit-remember command fired. A hollow pass (graph never executes,
    # all metrics zero) must fail here.
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    # Both scenarios were processed (the chat-path scenario did not abort the run).
    assert report["total_scenarios"] == 2, (
        f"expected both scenarios processed, got {report['total_scenarios']}"
    )
    persistence = {
        m["name"]: m for m in report.get("groups", {}).get("persistence_isolation", [])
    }
    # The memory_command node observed memory_operation == "remember".
    acc = persistence.get("explicit_operation_accuracy")
    assert acc and acc["numerator"] >= 1, (
        f"graph did not execute the memory command (explicit_operation_accuracy={acc})"
    )
    # "sales_region" landed as an active memory key after the turn.
    prov = persistence.get("evidence_provenance_completeness")
    assert prov and prov["numerator"] >= 1, (
        f"sales_region not observed as an active memory key "
        f"(evidence_provenance_completeness={prov})"
    )


@pytest.mark.asyncio
async def test_graph_multiturn_chat_path_runs_past_tenant_resolve(tmp_path):
    """A chat-path turn must clear resolve_tenant when run standalone.

    Chat-path turns route through the graph's ``resolve_tenant`` node
    (``TenantResolver.resolve`` → ``check_tenant_match``). The aggregate report
    does not serialize per-turn errors (ScenarioRunner absorbs them), so this
    test drives one chat turn through the bound stack directly and asserts a
    non-duplicate response with no error — proving the graph ran PAST
    tenant_resolve rather than crashing there.
    """
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        pytest.skip("TEST_DATABASE_URL not set to a test database")

    from eval.memory_eval.runner import (
        _bind_test_database,
        _make_restart_runtime,
        _restore_database,
        _seed_eval_fixtures,
        build_scripts_from_scenarios,
    )
    from eval.memory_eval.dataset import load_scenarios
    from eval.memory_eval.model_double import (
        DeterministicEmbeddingDouble,
        ScriptedModelDouble,
    )
    from eval.memory_eval.scenario_runner import ScenarioRunner
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.online_conversation import (
        close_online_runtime,
        initialize_online_runtime,
    )

    ds = tmp_path / "chat.jsonl"
    ds.write_text(
        json.dumps(
            {
                "id": "gm-chat-002",
                "version": 1,
                "tags": ["chat"],
                "turns": [
                    {
                        "input": "帮我看看福多多产品怎么讲",
                        "event_id": "gm-chat-002-1",
                        "expected": {"turn_relation": "new"},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    scenarios = load_scenarios(str(ds))
    double = ScriptedModelDouble(build_scripts_from_scenarios(scenarios))
    embedding_double = DeterministicEmbeddingDouble()

    test_db = os.environ["TEST_DATABASE_URL"]
    snapshot = await _bind_test_database(test_db, "eval-tenant")
    await _seed_eval_fixtures(get_session_factory, "eval-tenant", "eval-agent")
    restart_runtime = _make_restart_runtime()
    import uuid

    run_token = uuid.uuid4().hex[:8]
    await close_online_runtime()
    try:
        await initialize_online_runtime()
        s = scenarios[0]
        async with get_session_factory()() as db:
            ctx = {
                "db": db,
                "tenant_id": "eval-tenant",
                "agent_id": "eval-agent",
                "user_id": f"{s.id}-user-{run_token}",
                "session_user_id": f"{s.id}-session-{run_token}",
                "channel": "eval",
                "conversation_id": f"{s.id}-conv-{run_token}",
                "chat_model": double,
                "embedding_model": embedding_double,
            }
            run = await ScenarioRunner(ctx=ctx, restart_runtime=restart_runtime).run(s)
    finally:
        await close_online_runtime()
        await _restore_database(snapshot)

    # The chat turn ran past resolve_tenant: no error and a non-duplicate reply.
    assert run.error is None, f"chat turn errored (tenant_resolve?): {run.error}"
    assert run.observed, "no observed turns"
    obs = run.observed[0]
    assert obs.error is None, f"observed turn errored: {obs.error}"
    assert obs.result.get("response_kind") != "duplicate", (
        f"chat turn produced a duplicate response: {obs.result.get('response_kind')}"
    )
