"""Context Resolution Node — topic management and context-aware routing.

Integrates ``TopicManager`` and the Context Resolver into the Online
Conversation Graph:

1. Load / expire / restore a :class:`ConversationTopic` via ``TopicManager``.
2. Resolve pending clarifications when the user responds to a previous
   ambiguous turn.
3. Call the Context Resolver to classify the turn relation
   (continue / revise / switch / new / ambiguous).
4. Persist ambiguous state so the next user message can resolve it.

Returns either:

- ``context_status="resolved"`` — the query is self-contained; continue
  to evidence routing.
- ``context_status="clarify"`` — the query is ambiguous; return a
  clarification question to the user.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig

from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services.context_resolver import resolve_context
from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
)
from sales_agent.services.topic_manager import TopicManager, resolve_clarification

logger = logging.getLogger(__name__)

# Default clarification question returned when the resolver cannot
# determine the user's intent.
_DEFAULT_CLARIFICATION_QUESTION = (
    "请问您想继续之前的话题，还是开始一个新话题？"
)


def _unpack_context(config: RunnableConfig) -> dict[str, Any] | None:
    """Extract the runtime context dict from the LangGraph config."""
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime else None
    return ctx


# ---------------------------------------------------------------------------
# Resolve pending clarification
# ---------------------------------------------------------------------------


async def _resolve_pending_turn(
    *,
    manager: TopicManager,
    db: Any,
    topic: ConversationTopic,
    message: str,
    chat_model: Any,
    event_id: str,
    clar_resolver_fn: Any,
    now: datetime,
) -> dict[str, Any] | None:
    """Try to resolve a pending clarification on *topic*.

    Returns ``None`` when the clarification is still unresolved (system
    should ask again).  Returns a state-update dict when resolved.
    """
    pending_raw = topic.pending_clarification_json
    if not pending_raw:
        return None

    try:
        pending = json.loads(pending_raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid pending_clarification_json on topic %s", topic.id)
        return None

    # Duplicate event: same event_id as the one that created the pending
    # state — do not attempt to resolve again.
    if pending.get("event_id") == event_id:
        return {
            "response_kind": "duplicate",
            "context_status": "clarify",
        }

    # Call the clarification resolver
    decision = await clar_resolver_fn(
        message,
        chat_model,
        attempt_count=topic.clarification_attempts,
    )

    original_message = pending.get("original_message", message)
    candidate_query = pending.get("candidate_query", original_message)

    if decision.resolution == "continue":
        # Restore the original message; keep the topic active.
        await manager.resolve_pending(db, topic, decision, now=now)
        return {
            "context_status": "resolved",
            "topic_id": topic.id,
            "original_message": original_message,
            "standalone_query": candidate_query,
            "turn_relation": "continue",
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    if decision.resolution == "replace":
        # Complete replacement — use replacement_text as the new query.
        await manager.resolve_pending(db, topic, decision, now=now)
        replacement = decision.replacement_text or candidate_query
        return {
            "context_status": "resolved",
            "topic_id": topic.id,
            "original_message": original_message,
            "standalone_query": replacement,
            "turn_relation": "continue",
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    if decision.resolution == "new":
        # Close the topic; rerun the original message without old context.
        await manager.resolve_pending(db, topic, decision, now=now)
        return {
            "context_status": "resolved",
            "topic_id": None,
            "original_message": original_message,
            "standalone_query": original_message,
            "turn_relation": "new",
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    if decision.resolution == "cancel":
        # Cancel — close the topic and short-circuit to END so the
        # canceled query is NOT re-processed through evidence_routing -> chat.
        await manager.resolve_pending(db, topic, decision, now=now)
        return {
            "context_status": "cancel",
            "response_kind": "cancel",
            "topic_id": None,
            "original_message": original_message,
            "standalone_query": "",
            "turn_relation": "new",
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    # Unknown resolution — treat as still ambiguous, increment attempts.
    logger.warning("Unknown clarification resolution: %s", decision.resolution)
    return None


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------


async def context_resolution_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Resolve context / topic for the current user message.

    The node reads:

    - ``state["message"]`` — the user's current message text.
    - Runtime context via ``config`` for *db*, *chat_model*, *now*,
      *topic_manager*, *context_resolver_override*, and
      *clarification_resolver_override*.

    Returns a partial state update with ``context_status`` and related
    fields.
    """
    ctx = _unpack_context(config)
    db = ctx.get("db") if ctx else None
    chat_model = ctx.get("chat_model") if ctx else None
    now = ctx.get("now") or datetime.now(timezone.utc)

    # Allow dependency injection for testing
    manager: TopicManager = (
        ctx.get("topic_manager") if ctx and "topic_manager" in ctx
        else TopicManager()
    )
    context_resolver_fn = (
        ctx.get("context_resolver_override") if ctx else None
    ) or resolve_context
    clar_resolver_fn = (
        ctx.get("clarification_resolver_override") if ctx else None
    ) or resolve_clarification

    message = state.get("message", "")
    original_message = state.get("original_message") or message

    scope = {
        "tenant_id": state.get("tenant_id", ""),
        "agent_id": state.get("agent_id", ""),
        "user_id": state.get("user_id", ""),
        "channel": state.get("channel", ""),
    }
    conversation_id = state.get("conversation_id", "")
    event_id = state.get("event_id") or ""

    # ── Passthrough mode ──────────────────────────────────────────
    # When there is no DB and no injected topic_manager, skip topic
    # management entirely and just pass through as resolved.  This
    # allows existing tests to continue working without modification.
    if db is None and "topic_manager" not in (ctx or {}):
        return {
            "context_status": "resolved",
            "standalone_query": message,
            "original_message": message,
            "turn_relation": "continue",
        }

    # ── Step 1: Load the active topic ─────────────────────────────
    topic: ConversationTopic | None = await manager.get_active_topic(
        db, **scope,
    )

    # ── Step 2: Resolve pending clarification if present ──────────
    if topic and topic.pending_clarification_json:
        result = await _resolve_pending_turn(
            manager=manager,
            db=db,
            topic=topic,
            message=message,
            chat_model=chat_model,
            event_id=event_id,
            clar_resolver_fn=clar_resolver_fn,
            now=now,
        )
        if result is not None:
            return result
        # If result is None (unknown resolution), fall through to
        # re-classify as ambiguous.

    # ── Step 3: Check / apply topic expiry ────────────────────────
    previous_topic_id: str | None = None
    if topic:
        was_closed = await manager.close_if_expired(topic, now=now)
        if was_closed:
            previous_topic_id = topic.id
            topic = None

    # ── Step 4: Check restorable topics (no active topic) ─────────
    restorable_context: str | None = None
    if topic is None:
        restorable = await manager.find_restorable_topics(
            db, **scope, now=now,
        )
        if restorable:
            restorable_context = (
                f"There are {len(restorable)} restorable previous topic(s). "
                "Summaries: " + "; ".join(
                    t.summary for t in restorable[:3] if t.summary
                )
            )

    # ── Step 5: Call Context Resolver ─────────────────────────────
    # The online graph does NOT maintain a message history; pass
    # restorable topic info when available so the resolver can make
    # informed restore-vs-new decisions.
    recent_messages: list[dict[str, str]] = []
    if restorable_context:
        recent_messages.append({
            "role": "system",
            "content": restorable_context,
        })
    decision: ContextDecision = await context_resolver_fn(
        message=message,
        topic=topic,
        recent_messages=recent_messages,
        chat_model=chat_model,
    )

    # ── Step 6: Handle ambiguous result ───────────────────────────
    if decision.turn_relation == "ambiguous":
        if topic:
            await manager.set_pending_clarification(
                db,
                topic,
                event_id,
                message,
                decision.standalone_query,
            )

        return {
            "context_status": "clarify",
            "turn_relation": "ambiguous",
            "original_message": message,
            "standalone_query": "",
            "topic_id": topic.id if topic else None,
            "pending_clarification_id": event_id,
            "clarification_state": "pending",
            "answer_dict": {
                "summary": _DEFAULT_CLARIFICATION_QUESTION,
            },
            "response_kind": "clarify",
        }

    # ── Step 7: Apply resolved decision ───────────────────────────
    if topic is None:
        # No topic exists — create a new one.
        topic = await manager.create_topic(
            db,
            tenant_id=scope["tenant_id"],
            agent_id=scope["agent_id"],
            user_id=scope["user_id"],
            channel=scope["channel"],
            conversation_id=conversation_id,
            summary=decision.standalone_query or message,
            current_goal=decision.standalone_query or message,
            now=now,
        )
    else:
        # Propagate previous_topic_id for switch/revise transitions
        # so downstream consumers can detect topic changes.
        if decision.turn_relation in ("switch", "revise"):
            previous_topic_id = topic.id
        topic = await manager.apply_context_decision(
            db, topic, decision, now=now,
        )

    return {
        "context_status": "resolved",
        "topic_id": topic.id if topic else None,
        "previous_topic_id": previous_topic_id,
        "turn_relation": decision.turn_relation,
        "standalone_query": decision.standalone_query or message,
        "retained_entities": decision.retained_entities,
        "retracted_goals": decision.retracted_goals,
        "original_message": message,
        "pending_clarification_id": None,
        "clarification_state": None,
    }
