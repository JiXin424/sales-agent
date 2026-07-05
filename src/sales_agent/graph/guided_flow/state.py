"""State definition for the one-turn Guided Flow subgraph.

Every field is optional (``total=False``) because the subgraph may be
invoked with a partial payload; fields not set in a given ``ainvoke``
call carry over from the checkpoint.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class GuidedFlowState(TypedDict, total=False):
    """State passed through the guided-flow subgraph.

    Fields are grouped into three categories:

    - **Identity / routing**: carried from the parent graph or user input.
    - **Flow progress**: mutated by start / advance / cancel nodes.
    - **Output**: what the node produces for the calling layer.
    """

    # Identity / routing
    tenant_id: str
    agent_id: str
    user_id: str
    channel: str
    conversation_id: str
    message: str
    flow_action: str

    # Flow progress
    requested_flow: str | None
    active_flow: str | None
    flow_stage: str | None
    flow_payload: dict[str, Any]

    # Output
    answer_dict: dict[str, Any]
    response_kind: str
    completed_flow: str | None


__all__ = [
    "GuidedFlowState",
]
