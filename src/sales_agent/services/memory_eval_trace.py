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
