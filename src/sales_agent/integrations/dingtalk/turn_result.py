"""Frozen result dataclass for a single DingTalk turn (Task 6).

``handle_dingtalk_event`` returns a :class:`DingTalkTurnResult` for every
branch: fallback, media failure, reset, normal chat, clarification,
guided flow, and duplicate delivery. A duplicate has empty
``rendered_text`` and does NOT trigger a reply. On Graph/persistence
failure the processor sends a best-effort error reply and re-raises — it
never returns a success-shaped result from a failed transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DingTalkTurnResult:
    """Observable outcome of a DingTalk turn, populated for every branch."""

    event_id: str
    tenant_id: str
    agent_id: str | None
    internal_user_id: str | None
    dingtalk_user_id: str
    conversation_id: str | None
    thread_id: str | None
    normalized_message: str
    rendered_text: str
    answer_dict: dict[str, Any] = field(default_factory=dict)
    response_kind: str = ""
    topic_id: str | None = None
    previous_topic_id: str | None = None
    turn_relation: str | None = None
    standalone_query: str | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    completed_flow: str | None = None
    flow_action: str | None = None
    last_event_id: str | None = None
    latency_ms: int = 0
    error: str | None = None


__all__ = ["DingTalkTurnResult"]
