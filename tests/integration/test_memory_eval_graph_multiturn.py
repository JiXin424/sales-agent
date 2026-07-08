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

    # Minimal dataset: one explicit-remember scenario.
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
