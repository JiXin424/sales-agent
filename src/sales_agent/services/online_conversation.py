"""Runtime service for the Online Conversation Graph.

Provides:

- ``build_online_thread_id`` — stable thread ID without date
- ``build_online_turn_input`` — constructor for fresh turn input
- ``get_online_graph`` — cached compiled Online Graph (process-level
  PostgreSQL checkpoint singleton)
- ``invoke_online_turn`` — public API to process a single turn
- ``initialize_online_runtime`` — initialize the checkpoint and compile graph
- ``close_online_runtime`` — close the checkpoint and clear cached graph
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from sales_agent.core.config import get_settings
from sales_agent.graph.checkpoint_runtime import (
    CheckpointUnavailableError,
    close_production_checkpointer,
    initialize_production_checkpointer,
)
from sales_agent.graph.online.graph import build_online_graph
from sales_agent.graph.online.state import OnlineConversationState
from sales_agent.services.agent_service import resolve_tenant_agent_id
from sales_agent.services.online_turn_lock import acquire_online_turn_lock

logger = logging.getLogger(__name__)


# ====================================================================
# Module-level caches
# ====================================================================

_online_graph: CompiledStateGraph | None = None


# ====================================================================
# Turn envelope
# ====================================================================

TURN_SCOPED_DEFAULTS: dict[str, Any] = {
    "requested_flow": None,
    "flow_action": "chat",
    "completed_flow": None,
    "answer_dict": {},
    "response_kind": "pending",
    "previous_topic_id": None,
    "turn_relation": None,
    "standalone_query": None,
    "retained_entities": [],
    "retracted_goals": [],
    "pending_clarification_id": None,
    "clarification_state": None,
    "context_status": None,
    "original_message": None,
    "task_type": None,
    "route_confidence": None,
    "knowledge_policy": None,
    "knowledge_scope": [],
    "retrieval_query": None,
    "needs_retrieval": None,
    "route_trace": None,
}


def build_online_turn_input(
    *, tenant_id: str, agent_id: str, user_id: str,
    session_user_id: str, channel: str, conversation_id: str,
    message: str, entry_action: str | None, event_id: str | None,
    guided_flows_enabled: bool, topic_routing_enabled: bool,
    reset_requested: bool = False,
) -> OnlineConversationState:
    return {
        **copy.deepcopy(TURN_SCOPED_DEFAULTS),
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "session_user_id": session_user_id,
        "channel": channel,
        "conversation_id": conversation_id,
        "message": message,
        "entry_action": entry_action,
        "event_id": event_id,
        "guided_flows_enabled": guided_flows_enabled,
        "topic_routing_enabled": topic_routing_enabled,
        "reset_requested": reset_requested,
    }


# ====================================================================
# Online Graph compilation seam
# ====================================================================


def _compile_online_graph(checkpointer) -> CompiledStateGraph:
    """Compile the Online Graph with the given checkpointer."""
    return build_online_graph().compile(checkpointer=checkpointer)


# ====================================================================
# Online Graph lifecycle
# ====================================================================


async def initialize_online_runtime() -> CompiledStateGraph:
    """Initialize the Online Graph runtime: connect to checkpoint DB and compile graph.

    Idempotent: returns cached graph if already initialized.

    Returns:
        Compiled Online Graph with PostgreSQL checkpointer.

    Raises:
        CheckpointUnavailableError: if checkpoint DB connection fails.
    """
    global _online_graph
    if _online_graph is not None:
        return _online_graph
    saver = await initialize_production_checkpointer()
    _online_graph = _compile_online_graph(saver)
    return _online_graph


async def close_online_runtime() -> None:
    """Close the Online Graph runtime: close checkpoint DB pool and clear cached graph."""
    global _online_graph
    _online_graph = None
    await close_production_checkpointer()


# ====================================================================
# Thread ID
# ====================================================================


def build_online_thread_id(
    tenant_id: str,
    agent_id: str,
    channel: str,
    session_user_id: str,
) -> str:
    """Build a thread ID for the online conversation graph.

    Format:
        ``online:<tenant_id>:<agent_id>:<channel>:<session_user_id>``

    Stable across turns and dates.

    Args:
        tenant_id: Tenant identifier.
        agent_id: Agent identifier.
        channel: Communication channel (e.g. ``"dingtalk"``).
        session_user_id: End-user identifier for session scoping.

    Returns:
        A colon-delimited thread ID string.
    """
    return ":".join(
        ("online", tenant_id, agent_id, channel, session_user_id)
    )


# ====================================================================
# Online Graph singleton
# ====================================================================


def get_online_graph(*, checkpointer=None) -> CompiledStateGraph:
    """Return the compiled online conversation graph.

    The graph is compiled once and cached when using the default
    process-level PostgreSQL checkpointer.  Pass *checkpointer* to inject a
    specific checkpointer (for tests); production callers should omit it
    and use the cached initialized graph.

    Production callers **must not** create a new checkpointer per request
    — the process-level singleton is shared across all turns.

    Args:
        checkpointer: Optional LangGraph checkpointer.  If provided, compiles
            a fresh graph with this checkpointer (for testing only). If
            ``None``, returns the cached initialized graph (production path).

    Returns:
        A compiled :class:`CompiledStateGraph` ready for ``ainvoke``.

    Raises:
        CheckpointUnavailableError: if no checkpointer is provided and the
            runtime hasn't been initialized via ``initialize_online_runtime()``.
    """
    global _online_graph

    if checkpointer is not None:
        # Non-default checkpointer (e.g. from tests) — skip caching so
        # every call gets a fresh compilation with the injected saver.
        return _compile_online_graph(checkpointer)

    # Default path: use the cached initialized graph.
    if _online_graph is None:
        raise CheckpointUnavailableError(
            "Online Graph is unavailable before startup initialization"
        )
    return _online_graph


# ====================================================================
# Public API
# ====================================================================


async def invoke_online_turn(
    *,
    db,
    tenant_id: str,
    agent_id: str | None,
    user_id: str,
    session_user_id: str,
    channel: str,
    conversation_id: str,
    message: str,
    entry_action: str | None = None,
    event_id: str | None = None,
    chat_model=None,
    embedding_model=None,
    now: datetime | None = None,
    checkpointer=None,
) -> dict:
    """Process a single online conversation turn.

    This is the public production entry point.  It:

    1. Resolves the tenant agent via ``resolve_tenant_agent_id``.
    2. Reads ``guided_flows`` settings.
    3. Resolves model providers if *chat_model* / *embedding_model* are
       not supplied.
    4. Builds a date-scoped thread ID.
    5. Obtains (or reuses) the compiled Online Graph.
    6. Invokes the graph and returns the final state.

    Args:
        db: Async SQLAlchemy session.
        tenant_id: Tenant identifier.
        agent_id: Agent identifier (or ``None`` for the tenant default).
        user_id: Internal user identifier.
        session_user_id: Session-scoped user identifier (e.g. DingTalk
            ``staff_id``).
        channel: Communication channel (e.g. ``"dingtalk"``).
        conversation_id: Conversation thread identifier.
        message: User message text.
        entry_action: Optional entry action for flow triggering.
        event_id: Optional event ID for deduplication.
        chat_model: Chat model instance.  If ``None``, resolved from
            tenant config via ``TenantResolver``.
        embedding_model: Embedding model instance.  If ``None``,
            resolved from tenant config.
        now: Override datetime (for testing).
        checkpointer: Override checkpointer (for testing).

    Returns:
        The final graph state dict after processing the turn.
    """
    # 1. Resolve agent
    agent = await resolve_tenant_agent_id(db, tenant_id, agent_id)

    # 2. Get settings
    settings = get_settings()

    # 3. Resolve models if not provided — do this once here so that
    #    individual graph nodes never need to create providers.
    resolved_chat_model = chat_model
    resolved_embedding_model = embedding_model

    if resolved_chat_model is None or resolved_embedding_model is None:
        from sales_agent.services.tenant_resolver import TenantResolver

        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)
        if resolved_chat_model is None:
            resolved_chat_model = model_provider.chat
        if resolved_embedding_model is None:
            resolved_embedding_model = model_provider.embedding

    # 4. Build thread ID
    thread_id = build_online_thread_id(
        tenant_id,
        agent.id,
        channel,
        session_user_id,
    )

    # 4b. Acquire the transaction-scoped advisory lock for this thread
    # BEFORE invoking the Graph. This serializes same-thread turns across
    # worker processes: a concurrent worker delivering a turn for the same
    # thread blocks here until this caller's transaction commits/rolls
    # back, guaranteeing the second worker sees the latest checkpoint.
    # The caller owns the transaction and must commit/rollback after the
    # Graph invocation and persistence complete; this acquires the lock
    # but does not manage the transaction lifecycle.
    await acquire_online_turn_lock(db, thread_id)

    # 5. Get graph
    graph = get_online_graph(checkpointer=checkpointer)

    # 6. Build input
    input_state = build_online_turn_input(
        tenant_id=tenant_id,
        agent_id=agent.id,
        user_id=user_id,
        session_user_id=session_user_id,
        channel=channel,
        conversation_id=conversation_id,
        message=message,
        entry_action=entry_action,
        event_id=event_id,
        guided_flows_enabled=settings.guided_flows.enabled,
        topic_routing_enabled=settings.topic_routing.enabled,
    )

    # 7. Invoke
    result = await graph.ainvoke(
        input_state,
        config={"configurable": {"thread_id": thread_id}},
        context={
            "db": db,
            "chat_model": resolved_chat_model,
            "embedding_model": resolved_embedding_model,
            "now": now,
        },
    )

    # Thread the thread_id into the result so callers/tests can observe
    # identity. Harmless for existing callers that use result.get(...).
    return {**result, "thread_id": thread_id}
