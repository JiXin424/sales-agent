"""Runtime service for the Online Conversation Graph.

Provides:

- ``build_online_thread_id`` — stable thread ID without date
- ``build_online_turn_input`` — constructor for fresh turn input
- ``get_online_graph`` — cached compiled Online Graph (process-level
  PostgreSQL checkpoint singleton)
- ``resolve_online_models`` — resolve chat + embedding models for a tenant
- ``prepare_online_turn`` — resolve agent/models/graph + build stable thread
  ID and new-turn input WITHOUT invoking the Graph (Task 6)
- ``invoke_online_turn`` — public API to process a single turn
- ``initialize_online_runtime`` — initialize the checkpoint and compile graph
- ``close_online_runtime`` — close the checkpoint and clear cached graph
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
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
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)


# =============================================================# Module-level caches
# =============================================================
_online_graph: CompiledStateGraph | None = None


# =============================================================# Turn envelope
# =============================================================
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
    # Long-term memory defaults
    "memory_operation": None,
    "memory_status": None,
    "memory_reason_code": None,
    "memory_ids": [],
    "memory_candidate_count": 0,
    # User profile memory defaults (Task 5)
    "user_profile_memory_enabled": False,
    "user_memory_context": None,
    "selected_memory_ids": [],
    "memory_trace": {},
    "memory_degraded": False,
    "memory_degradation_reason": None,
    "profile_version": None,
}


def build_online_turn_input(
    *, tenant_id: str, agent_id: str, user_id: str,
    session_user_id: str, channel: str, conversation_id: str,
    message: str, entry_action: str | None, event_id: str | None,
    guided_flows_enabled: bool, topic_routing_enabled: bool,
    scenario_coach_enabled: bool = False,
    reset_requested: bool = False,
    long_term_memory_enabled: bool = False,
    user_profile_memory_enabled: bool = False,
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
        "scenario_coach_enabled": scenario_coach_enabled,
        "reset_requested": reset_requested,
        "long_term_memory_enabled": long_term_memory_enabled,
        "user_profile_memory_enabled": user_profile_memory_enabled,
    }


# =============================================================# Online Graph compilation seam
# =============================================================

def _compile_online_graph(checkpointer) -> CompiledStateGraph:
    """Compile the Online Graph with the given checkpointer."""
    return build_online_graph().compile(checkpointer=checkpointer)


# =============================================================# Online Graph lifecycle
# =============================================================

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


# =============================================================# Thread ID
# =============================================================

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


# =============================================================# Online Graph singleton
# =============================================================

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


# =============================================================# Prepared turn contract (Task 6)
# =============================================================

@dataclass(frozen=True)
class PreparedOnlineTurn:
    """Immutable bundle of everything needed to invoke the Online Graph.

    Resolved once by ``prepare_online_turn`` so that the standard
    (``invoke_online_turn``) and streaming (``handle_dingtalk_stream_via_graph``)
    paths share identical thread ID, input state, config, context, and Graph.
    Neither path builds its own thread ID or duplicates the input dict.
    """

    graph: CompiledStateGraph
    thread_id: str
    input_state: OnlineConversationState
    config: dict[str, Any]
    context: dict[str, Any]


async def resolve_online_models(
    *, db, tenant_id: str, chat_model=None, embedding_model=None,
) -> tuple[Any, Any]:
    """Resolve the chat + embedding models for a tenant.

    Returns the injected pair as-is when both are provided; otherwise
    resolves the missing one(s) from the tenant config via
    :class:`TenantResolver`.
    """
    if chat_model is not None and embedding_model is not None:
        return chat_model, embedding_model
    resolver = TenantResolver(db)
    tenant_info = await resolver.resolve(tenant_id)
    provider = resolver.get_model_provider(tenant_info)
    return chat_model or provider.chat, embedding_model or provider.embedding


async def prepare_online_turn(
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
    reset_requested: bool = False,
    chat_model=None,
    embedding_model=None,
    now: datetime | None = None,
    checkpointer=None,
) -> PreparedOnlineTurn:
    """Resolve the agent + models, build the stable thread ID and new-turn
    input, and select the strict cached/test-injected Graph.

    This does NOT invoke or stream — it returns a
    :class:`PreparedOnlineTurn` that the caller feeds to
    ``graph.ainvoke``/``graph.astream`` after acquiring the advisory lock.
    Centralising this here guarantees the standard and streaming paths use
    the same thread ID, input, config, context, and Graph.
    """
    agent = await resolve_tenant_agent_id(db, tenant_id, agent_id)
    resolved_chat, resolved_embedding = await resolve_online_models(
        db=db,
        tenant_id=tenant_id,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    thread_id = build_online_thread_id(
        tenant_id, agent.id, channel, session_user_id,
    )
    settings = get_settings()
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
        scenario_coach_enabled=settings.scenario_coach.enabled,
        reset_requested=reset_requested,
        long_term_memory_enabled=settings.long_term_memory.enabled,
        user_profile_memory_enabled=settings.user_profile_memory.enabled,
    )
    return PreparedOnlineTurn(
        graph=get_online_graph(checkpointer=checkpointer),
        thread_id=thread_id,
        input_state=input_state,
        config={"configurable": {"thread_id": thread_id}},
        context={
            "db": db,
            "chat_model": resolved_chat,
            "embedding_model": resolved_embedding,
            "now": now,
        },
    )


# =============================================================# Public API
# =============================================================

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
    reset_requested: bool = False,
    chat_model=None,
    embedding_model=None,
    now: datetime | None = None,
    checkpointer=None,
) -> dict:
    """Process a single online conversation turn.

    This is the public production entry point.  It prepares the turn
    (resolve agent + models, build thread ID + input, select Graph),
    acquires the transaction-scoped advisory lock for the thread, invokes
    the Graph, and returns the final state with ``thread_id`` merged in.

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
        reset_requested: When ``True``, the Graph clears active flow/topic
            state before processing (Task 6 reset semantics).
        chat_model: Chat model instance.  If ``None``, resolved from
            tenant config via ``TenantResolver``.
        embedding_model: Embedding model instance.  If ``None``,
            resolved from tenant config.
        now: Override datetime (for testing).
        checkpointer: Override checkpointer (for testing).

    Returns:
        The final graph state dict after processing the turn.
    """
    prepared = await prepare_online_turn(
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        session_user_id=session_user_id,
        channel=channel,
        conversation_id=conversation_id,
        message=message,
        entry_action=entry_action,
        event_id=event_id,
        reset_requested=reset_requested,
        chat_model=chat_model,
        embedding_model=embedding_model,
        now=now,
        checkpointer=checkpointer,
    )

    # Acquire the transaction-scoped advisory lock for this thread BEFORE
    # invoking the Graph. This serializes same-thread turns across worker
    # processes. The caller owns the transaction and must commit/rollback
    # after the Graph invocation and persistence complete; this acquires
    # the lock but does not manage the transaction lifecycle.
    await acquire_online_turn_lock(db, prepared.thread_id)

    graph_result = await prepared.graph.ainvoke(
        prepared.input_state,
        prepared.config,
        context=prepared.context,
    )

    # Thread the thread_id into the result so callers/tests can observe
    # identity. Harmless for existing callers that use result.get(...).
    return {**graph_result, "thread_id": prepared.thread_id}
