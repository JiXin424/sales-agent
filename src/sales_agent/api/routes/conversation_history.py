"""Conversation History API — read-only checkpoint time-travel for REAL
production conversations (DingTalk stream).

Unlike ``graph_debug.py`` — which restricts history endpoints to
``debug:``-prefixed threads (``_ensure_debug_thread``) — these endpoints read
back the checkpoint timeline of any **real** conversation by using its
``conversation_id`` directly as the LangGraph ``thread_id``.

Why this works: the DingTalk production path
(``integrations/dingtalk/graph_stream.py``) compiles the Online Graph with the
shared ``AsyncPostgresSaver`` and runs each turn under
``thread_id = conversation_id``. That same ``conversation_id`` is the primary
key of the ``conversations`` table, so the PG checkpoints for a real
conversation are directly addressable.

STRICTLY READ-ONLY — only GET endpoints. No state edit, no fork, no replay.
Production execution paths are untouched ("先保持现版本").
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from sales_agent.api.deps import DbSession
from sales_agent.models.conversation import Conversation
# Reuse graph_debug's compiled-graph/checkpointer helpers + response models.
# These are stable module-level helpers (no __all__ restriction); importing
# them keeps the two read-only views DRY instead of duplicating the
# checkpoint-walking logic. graph_debug does not import this module, so there
# is no circular-import risk.
from sales_agent.api.routes.graph_debug import (
    CheckpointListResponse,
    CheckpointStateResponse,
    CheckpointSummary,
    _checkpoint_node_label,
    _compile_with_checkpointer,
    _safe_serialize,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/{agent_id}/history", tags=["conversation-history"])

# Production conversations run on the Online Graph, so its node-boundary
# checkpoints are what we read back. Granularity = Online Graph node level
# (chat / guided_flow subgraphs carry no checkpointer of their own).
PROD_GRAPH_ID = "online"


# ── Pydantic models ────────────────────────────────────────────────

class ConversationHistoryItem(BaseModel):
    """One row in the history-page conversation picker."""

    conversation_id: str
    message: str
    channel: str | None = None
    task_type: str | None = None
    status: str | None = None
    updated_at: str | None = None


class ConversationHistoryListResponse(BaseModel):
    conversations: list[ConversationHistoryItem]
    total: int
    limit: int
    offset: int


# ── Routes ─────────────────────────────────────────────────────────

@router.get("/conversations", response_model=ConversationHistoryListResponse)
async def list_conversations(
    agent_id: str,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List recent real conversations for an agent (newest first).

    Reads the ``conversations`` table (one row per ``conversation_id``,
    updated in place each turn). Drives the history-page picker. Returns an
    empty list (not an error) when the agent has no conversations.
    """
    total = await db.scalar(
        select(func.count(Conversation.id)).where(Conversation.agent_id == agent_id)
    )

    stmt = (
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return ConversationHistoryListResponse(
        conversations=[
            ConversationHistoryItem(
                conversation_id=r.id,
                message=r.message or "",
                channel=r.channel,
                task_type=r.task_type,
                status=r.status,
                updated_at=r.updated_at.isoformat() if r.updated_at else None,
            )
            for r in rows
        ],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/conversations/{conversation_id}/checkpoints",
    response_model=CheckpointListResponse,
)
async def list_conversation_checkpoints(
    agent_id: str,  # noqa: ARG001 — keeps the agent-scoped URL; checkpoints are keyed by thread_id
    conversation_id: str,
):
    """Read-only checkpoint timeline for a real conversation.

    Uses ``conversation_id`` directly as the LangGraph ``thread_id`` — **no**
    ``debug:`` prefix enforcement (unlike ``graph_debug``). Returns the
    Online-Graph node-boundary checkpoints persisted by the DingTalk
    production path, sorted by step ascending.
    """
    graph = await _compile_with_checkpointer(PROD_GRAPH_ID)

    snapshots = []
    async for snapshot in graph.aget_state_history(
        {"configurable": {"thread_id": conversation_id}}
    ):
        snapshots.append(snapshot)

    summaries: list[CheckpointSummary] = []
    for snapshot in snapshots:
        metadata = snapshot.metadata or {}
        summaries.append(CheckpointSummary(
            checkpoint_id=(snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
            step=metadata.get("step") if isinstance(metadata, dict) else None,
            node=_checkpoint_node_label(snapshot),
            ts=snapshot.created_at,
            next=list(snapshot.next) if snapshot.next else None,
            parent_checkpoint_id=(snapshot.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
        ))

    # aget_state_history yields newest-first; sort by step ascending (None last).
    summaries.sort(key=lambda s: (s.step is None, s.step if s.step is not None else 0))
    return CheckpointListResponse(checkpoints=summaries)


@router.get(
    "/conversations/{conversation_id}/checkpoints/{checkpoint_id}/state",
    response_model=CheckpointStateResponse,
)
async def get_conversation_checkpoint_state(
    agent_id: str,  # noqa: ARG001 — agent-scoped URL; state is keyed by checkpoint_id
    conversation_id: str,
    checkpoint_id: str,
):
    """Read-only full state values for one checkpoint of a real conversation."""
    graph = await _compile_with_checkpointer(PROD_GRAPH_ID)

    async for snapshot in graph.aget_state_history(
        {"configurable": {"thread_id": conversation_id}}
    ):
        snap_cp_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
        if snap_cp_id == checkpoint_id:
            return CheckpointStateResponse(
                checkpoint_id=snap_cp_id,
                values=_safe_serialize(snapshot.values),
                next=list(snapshot.next) if snapshot.next else None,
            )

    raise HTTPException(
        404,
        f"Checkpoint '{checkpoint_id}' not found for conversation '{conversation_id}'.",
    )
