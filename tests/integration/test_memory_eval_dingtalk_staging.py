"""Integration: dingtalk-staging mode drives the real DingTalk processor (Spec 4 §3.4).

This is a stretch verification for Task 14. It drives the real
``handle_dingtalk_event`` with staging users against the test DB, using
deterministic model doubles. It requires a reachable test Postgres and a seeded
tenant/agent; when the environment is not wired for that it reports the gap
rather than masking it.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dingtalk_staging_captures_public_reply(tmp_path):
    if "test" not in os.environ.get("TEST_DATABASE_URL", ""):
        pytest.skip("TEST_DATABASE_URL not set to a test database")

    # Dataset mixes an explicit-remember scenario (memory_command, bypasses
    # tenant_resolve) with a chat-path scenario that routes through the chat
    # node → resolve_tenant → TenantResolver.resolve → check_tenant_match.
    # The chat-path turn is the regression guard for the standalone-CLI
    # tenant-mismatch defect: run_dingtalk_staging's turn loop has no
    # try/except, so a TenantMismatchError would propagate and fail this test
    # (rather than being silently absorbed).
    ds = tmp_path / "ds.jsonl"
    ds.write_text(
        json.dumps(
            {
                "id": "dt-001",
                "version": 1,
                "tags": ["explicit", "remember"],
                "turns": [
                    {
                        "input": "记住我负责华东区",
                        "event_id": "dt-001-1",
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
                "id": "dt-chat-001",
                "version": 1,
                "tags": ["chat"],
                "turns": [
                    {
                        "input": "帮我看看福多多产品怎么讲",
                        # Unique per invocation: the chat path enqueues an
                        # ``infer_candidates`` row keyed by (tenant, event_id),
                        # so a deterministic id would collide on the persistent
                        # test DB across re-runs.
                        "event_id": f"dt-chat-001-1-{uuid.uuid4().hex[:8]}",
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

    from eval.memory_eval.runner import run_dingtalk_staging
    from types import SimpleNamespace

    rc = await run_dingtalk_staging(
        SimpleNamespace(
            dataset=str(ds),
            output=str(tmp_path / "out"),
            tenant_id="eval-tenant",
            agent_id="eval-agent",
        )
    )
    # Exit code 0 or 1 (quality), never 2 (invalid execution) on a valid setup.
    assert rc in (0, 1), f"unexpected rc={rc}"
    assert (tmp_path / "out" / "report.json").exists(), "report.json not written"

    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    # Both scenarios processed — the chat-path turn (which hits resolve_tenant)
    # did not abort the run with TenantMismatchError.
    assert report["total_scenarios"] == 2, (
        f"expected both scenarios processed, got {report['total_scenarios']}"
    )

    # Non-hollow execution: the staging runner must have genuinely driven the
    # DingTalk processor through the Online Graph. The memory_command node
    # observed memory_operation == "remember" and produced a public reply.
    persistence = {
        m["name"]: m for m in report.get("groups", {}).get("persistence_isolation", [])
    }
    acc = persistence.get("explicit_operation_accuracy")
    assert acc and acc["numerator"] >= 1, (
        f"DingTalk processor did not execute the memory command "
        f"(explicit_operation_accuracy={acc})"
    )
    # The memory key "sales_region" was captured (proves DB write + read-back).
    prov = persistence.get("evidence_provenance_completeness")
    assert prov and prov["numerator"] >= 1, (
        f"sales_region not observed as an active memory key "
        f"(evidence_provenance_completeness={prov})"
    )

    # The processor genuinely ran and rendered a reply: latency_ms > 0 in the
    # trajectory group proves the processor completed normally (not a
    # duplicate/error branch which have zero rendering work).
    trajectory = {
        m["name"]: m for m in report.get("groups", {}).get("trajectory", [])
    }
    lat = trajectory.get("p50_latency_ms")
    assert lat and lat["score"] > 0, (
        f"processor did not produce a timed result (p50_latency_ms={lat})"
    )


@pytest.mark.asyncio
async def test_dingtalk_staging_refuses_non_test_db(tmp_path, monkeypatch):
    """Without a test DB URL, the mode must refuse execution (rc=2, §11)."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    ds = tmp_path / "ds.jsonl"
    ds.write_text(
        json.dumps(
            {
                "id": "dt-guard",
                "tags": ["explicit"],
                "turns": [
                    {
                        "input": "hi",
                        "expected": {"reply_contains": ["hi"]},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    from eval.memory_eval.runner import run_dingtalk_staging
    from types import SimpleNamespace

    rc = await run_dingtalk_staging(
        SimpleNamespace(
            dataset=str(ds),
            output=str(tmp_path / "out"),
            tenant_id="eval-tenant",
            agent_id="eval-agent",
        )
    )
    assert rc == 2, f"expected rc=2 for non-test DB, got {rc}"
