"""Runtime service for the Online Conversation Graph.

Provides:

- ``build_online_thread_id`` — thread ID with date in Shanghai timezone
- ``get_online_graph`` — cached compiled Online Graph (process-level
  ``InMemorySaver`` singleton)
- ``invoke_online_turn`` — public API to process a single turn
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.graph.state import CompiledStateGraph

from sales_agent.core.config import get_settings
from sales_agent.graph.online_graph import build_online_graph
from sales_agent.services.agent_service import resolve_tenant_agent_id

logger = logging.getLogger(__name__)


# ====================================================================
# Module-level caches
# ====================================================================

_online_graph: CompiledStateGraph | None = None
_online_checkpointer = None  # process-level InMemorySaver


# ====================================================================
# Thread ID
# ====================================================================


def build_online_thread_id(
    tenant_id: str,
    agent_id: str,
    channel: str,
    session_user_id: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    """Build a thread ID for the online conversation graph.

    Format:
        ``online:<tenant_id>:<agent_id>:<channel>:<session_user_id>:<YYYY-MM-DD>``

    The date component is computed in the given timezone (default
    ``Asia/Shanghai``) so that a user's conversation resets at midnight
    Shanghai time regardless of the server's clock.

    Args:
        tenant_id: Tenant identifier.
        agent_id: Agent identifier.
        channel: Communication channel (e.g. ``"dingtalk"``).
        session_user_id: End-user identifier for session scoping.
        now: Override datetime (for testing).  If omitted, uses
            ``datetime.now(zone)``.
        timezone_name: IANA timezone name (default ``"Asia/Shanghai"``).

    Returns:
        A colon-delimited thread ID string.
    """
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    return ":".join(
        ("online", tenant_id, agent_id, channel, session_user_id, current.date().isoformat())
    )


# ====================================================================
# Online Graph singleton
# ====================================================================


def get_online_graph(*, checkpointer=None) -> CompiledStateGraph:
    """Return the compiled online conversation graph.

    The graph is compiled once and cached.  Pass *checkpointer* to inject
    a specific checkpointer (for tests); production callers should omit
    it and let the function use the process-level default.

    Production callers **must not** create a new checkpointer per request
    — the process-level singleton is shared across all turns.

    Args:
        checkpointer: Optional LangGraph checkpointer.  If ``None``, a
            process-level :class:`InMemorySaver` singleton is used.

    Returns:
        A compiled :class:`CompiledStateGraph` ready for ``ainvoke``.
    """
    global _online_graph, _online_checkpointer

    if _online_graph is not None:
        return _online_graph

    if checkpointer is None:
        # Lazily create the process-level singleton
        if _online_checkpointer is None:
            from langgraph.checkpoint.memory import InMemorySaver

            _online_checkpointer = InMemorySaver()
        checkpointer = _online_checkpointer

    _online_graph = build_online_graph().compile(checkpointer=checkpointer)
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
        now=now,
        timezone_name=settings.guided_flows.timezone,
    )

    # 5. Get graph
    graph = get_online_graph(checkpointer=checkpointer)

    # 6. Build input
    input_state: dict[str, Any] = {
        "tenant_id": tenant_id,
        "agent_id": agent.id,
        "user_id": user_id,
        "session_user_id": session_user_id,
        "channel": channel,
        "conversation_id": conversation_id,
        "message": message,
        "entry_action": entry_action,
        "event_id": event_id,
        "guided_flows_enabled": settings.guided_flows.enabled,
    }

    # 7. Invoke
    return await graph.ainvoke(
        input_state,
        config={"configurable": {"thread_id": thread_id}},
        context={
            "db": db,
            "chat_model": resolved_chat_model,
            "embedding_model": resolved_embedding_model,
        },
    )
