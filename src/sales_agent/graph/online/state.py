"""Narrow state definition for the Online Conversation Graph.

This state must NOT inherit ``ChatGraphState`` — only contains the fields
needed for routing, guided-flow tracking, and deduplication.
"""

from __future__ import annotations

from datetime import datetime
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
    flow_action: str  # "duplicate" | "start" | "cancel" | "advance" | "chat" | "direct_chat" | "reset" | "memory_command" | "profile_transparency" | "sales_action" | "sales_action_observe"
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

    # ── Sales Actions (Task 4) ────────────────────────────────────
    # Per-turn input flag (mirrors long_term_memory_enabled): when False the
    # graph does NOT route to sales-action nodes even on an explicit command
    # (matches the runbook's "enabled=false -> Online Graph 不路由销售动作").
    sales_actions_enabled: bool
    # Per-turn outputs (reset each turn via TURN_SCOPED_DEFAULTS).
    sales_action_operation: str | None  # create/complete/cancel/snooze/list/clarify/suggest/ignore
    sales_action_status: str | None  # created/done/cancelled/snoozed/clarify/empty/failed…
    sales_action_id: str | None
    sales_action_scheduled_at: datetime | None
    suggested_sales_action: dict[str, Any] | None  # post-chat suggestion metadata
    sales_action_suggestion_enabled: bool
    # Cross-turn: set on a clarify turn so the next turn routes back to the
    # sales-action node to complete/re-clarify/abandon. Deliberately NOT in
    # TURN_SCOPED_DEFAULTS so the checkpoint value survives across turns.
    sales_action_pending_clarification: str | None
    # Cross-turn: the partial SalesActionExtraction (serialized via
    # ``model_dump()``) stashed on a clarify turn, so the follow-up turn can
    # merge the user's answer (e.g. "下午3点") against the stored title/customer.
    # Deliberately NOT in TURN_SCOPED_DEFAULTS so the checkpoint value survives
    # across turns; cleared on successful completion or chat fall-through.
    sales_action_pending_partial: dict[str, Any] | None
    # reason_code mirror for the DingTalk turn result.
    sales_action_reason_code: str | None

    # ── Pursuit Loop (Plan → Observe → Replan) ─────────────────────
    # Per-turn gate (mirrors sales_actions_enabled).
    pursuit_loop_enabled: bool
    # Cross-turn: set after an action is completed without an inline outcome;
    # the next turn routes to observe. NOT in TURN_SCOPED_DEFAULTS.
    pending_observe_action_id: str | None
    # Per-turn: output of replan_node (the suggested next action, if any).
    replan_suggestion: dict[str, Any] | None
    # Per-turn: list of action IDs cancelled by the replan step (for audit).
    replan_cancelled_ids: list[str]


__all__ = [
    "OnlineConversationState",
]
