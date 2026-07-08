from __future__ import annotations

from sales_agent.services.memory_eval_trace import (
    build_eval_trace,
    hash_scope,
    hash_thread_id,
)


def test_hash_scope_is_stable_and_irreversible():
    a = hash_scope("tenant-1", "agent-1", "user-1")
    b = hash_scope("tenant-1", "agent-1", "user-1")
    assert a == b
    assert a.startswith("h:")
    assert "user-1" not in a


def test_hash_thread_id_is_deterministic_and_irreversible():
    raw = "online:tenant-1:agent-1:dt:user-1"
    a = hash_thread_id(raw)
    b = hash_thread_id(raw)
    assert a == b
    assert a.startswith("h:")
    assert len(a) == 26  # "h:" + 24 hex chars
    # Plaintext scope tokens must not appear in the hash output.
    assert "user-1" not in a
    assert "tenant-1" not in a
    # Different thread_ids produce different hashes.
    assert hash_thread_id("online:tenant-1:agent-1:dt:other-user") != a


def test_build_eval_trace_captures_section_8_fields():
    state = {
        "tenant_id": "tenant-1", "agent_id": "agent-1", "user_id": "user-1",
        "topic_id": "topic-7", "turn_relation": "switch",
        "thread_id": "online:tenant-1:agent-1:dt:user-1",
        "checkpoint_version": 3,
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
    assert trace["thread_id"].startswith("h:")
    assert "user-1" not in trace["thread_id"]
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
