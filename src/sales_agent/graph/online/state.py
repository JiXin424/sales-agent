"""Narrow state definition for the Online Conversation Graph.

This state must NOT inherit ``ChatGraphState`` — only contains the fields
needed for routing, guided-flow tracking, and deduplication.
"""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class OnlineConversationState(TypedDict, total=False):
    """State for the unified online conversation graph.

    All fields are optional (``total=False``) so that carry-over from
    checkpoints works naturally — only the fields that changed on a
    given turn need to be returned by a node.
    """

    # ── Identity / Input ──────────────────────────────────────────
    tenant_id: str
    agent_id: str
    user_id: str
    session_user_id: str
    channel: str
    conversation_id: str
    message: str
    entry_action: str | None
    event_id: str | None

    # ── Guided-flow control ───────────────────────────────────────
    guided_flows_enabled: bool
    topic_routing_enabled: bool
    scenario_coach_enabled: bool
    requested_flow: str | None
    flow_action: str  # "duplicate" | "start" | "cancel" | "advance" | "chat" | "direct_chat" | "reset" | "memory_command" | "profile_transparency"
    active_flow: str | None
    flow_stage: str | None
    flow_payload: dict[str, Any]
    completed_flow: str | None

    # ── Reset / topic-control ─────────────────────────────────────
    reset_requested: bool
    force_new_topic: bool

    # ── Output ────────────────────────────────────────────────────
    answer_dict: dict[str, Any]
    response_kind: str
    # Chat Graph 末态透传（供 eval / 钉钉消费者直接从返回值取）
    sources: list[dict]
    risk_result: dict[str, Any]
    usage: dict[str, int]
    path: str | None

    # ── Deduplication ─────────────────────────────────────────────
    last_event_id: str | None

    # ── Topic / Context Resolution ────────────────────────────────
    topic_id: str | None
    previous_topic_id: str | None
    turn_relation: str | None  # "continue" | "revise" | "switch" | "new" | "ambiguous"
    standalone_query: str | None
    retained_entities: list[str]
    retracted_goals: list[str]
    pending_clarification_id: str | None
    clarification_state: str | None
    context_status: str | None  # "resolved" | "clarify"
    original_message: str | None

    # ── Evidence Routing ──────────────────────────────────────────
    task_type: str | None
    route_confidence: float | None
    knowledge_policy: str | None  # "none" | "optional" | "required"
    knowledge_scope: list[str]
    retrieval_query: str | None
    needs_retrieval: bool | None
    route_trace: str | None

    # ── Long-term Memory ──────────────────────────────────────────
    long_term_memory_enabled: bool
    memory_operation: str | None
    memory_status: str | None
    memory_reason_code: str | None
    memory_ids: list[str]
    memory_candidate_count: int

    # ── User Profile Memory (Task 5) ──────────────────────────────
    user_profile_memory_enabled: bool
    user_memory_context: str | None
    selected_memory_ids: list[str]
    memory_trace: dict[str, Any]
    memory_degraded: bool
    memory_degradation_reason: str | None
    profile_version: int | None


__all__ = [
    "OnlineConversationState",
]
