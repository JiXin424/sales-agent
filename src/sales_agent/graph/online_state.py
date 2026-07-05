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
    requested_flow: str | None
    flow_action: str  # "duplicate" | "start" | "cancel" | "advance" | "chat"
    active_flow: str | None
    flow_stage: str | None
    flow_payload: dict[str, Any]
    completed_flow: str | None

    # ── Output ────────────────────────────────────────────────────
    answer_dict: dict[str, Any]
    response_kind: str

    # ── Deduplication ─────────────────────────────────────────────
    last_event_id: str | None


__all__ = [
    "OnlineConversationState",
]
