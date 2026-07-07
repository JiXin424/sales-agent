"""Node functions for the Online Conversation Graph.

All node functions live here so ``online/graph.py`` contains only the
graph builder.  This avoids circular imports (``chat_node`` needs the
cached subgraph factories, which in turn need the subgraph builders).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from sales_agent.graph.chat.graph import build_chat_graph
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
from sales_agent.graph.guided_flow.triggers import is_cancel_command, resolve_requested_flow
from sales_agent.graph.online.state import OnlineConversationState
from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services import conversation_logger
from sales_agent.services.context_resolver import resolve_context
from sales_agent.services.evidence_router import route_intent_evidence
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

# ====================================================================
# Module-level subgraph caches
# ====================================================================

_guided_flow_graph: CompiledStateGraph | None = None
_chat_graph: CompiledStateGraph | None = None


# ====================================================================
# Helpers
# ====================================================================


def _unpack_context(config: RunnableConfig) -> dict[str, Any] | None:
    """Extract the runtime context dict from the LangGraph config."""
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime else None
    return ctx


def _get_guided_flow_graph() -> CompiledStateGraph:
    """Return a cached compiled Guided Flow subgraph (no checkpointer).

    Because the parent (Online Graph) owns the checkpointer, the subgraph
    must be compiled without one so that checkpoint writes happen at the
    parent level only.
    """
    global _guided_flow_graph
    if _guided_flow_graph is None:
        _guided_flow_graph = build_guided_flow_graph().compile()
    return _guided_flow_graph


def _get_chat_graph() -> CompiledStateGraph:
    """Return a cached compiled Chat Graph (no checkpointer)."""
    global _chat_graph
    if _chat_graph is None:
        _chat_graph = build_chat_graph().compile()
    return _chat_graph


# ====================================================================
# normalize_turn
# ====================================================================


def normalize_turn_node(state: OnlineConversationState) -> dict[str, Any]:
    """Set ``requested_flow`` on every turn and choose ``flow_action``.

    Routing priority (first match wins):

    1. **Duplicate** — ``event_id`` matches ``last_event_id``.
    2. **Start** — guided flows enabled & a requested-flow trigger.
    3. **Cancel** — guided flows enabled & active flow & cancel command.
    4. **Advance** — guided flows enabled & active flow.
    5. **Chat** — default.
    """
    guided_enabled = state.get("guided_flows_enabled", False)
    message = state.get("message", "")
    entry_action = state.get("entry_action")

    # Always resolve *requested_flow* so the field is explicit on every turn
    requested_flow: str | None = None
    if guided_enabled:
        requested_flow = resolve_requested_flow(message=message, entry_action=entry_action)

    # ── Choose flow_action ────────────────────────────────────────
    event_id = state.get("event_id")
    last_event_id = state.get("last_event_id")

    if event_id and event_id == last_event_id:
        flow_action = "duplicate"
    elif guided_enabled and requested_flow:
        flow_action = "start"
    elif guided_enabled and state.get("active_flow") and is_cancel_command(message):
        flow_action = "cancel"
    elif guided_enabled and state.get("active_flow"):
        flow_action = "advance"
    else:
        flow_action = "chat"

    # When topic routing is disabled, bypass context resolution and
    # evidence routing — but still classify intent via direct_evidence_routing
    # so knowledge questions retrieve. Send chat messages to the chat node
    # without context rewriting / clarify / topic management.
    topic_routing_enabled = state.get("topic_routing_enabled", False)
    if flow_action == "chat" and not topic_routing_enabled:
        flow_action = "direct_chat"

    return {
        "requested_flow": requested_flow,
        "flow_action": flow_action,
    }


# ====================================================================
# chat_node
# ====================================================================


async def chat_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Invoke the cached Chat Graph and map stable response fields.

    Uses ``standalone_query`` as the Chat message (the context-resolved
    or rewritten form), and preserves ``original_message`` for logging.

    The Chat Graph handles its own conversation logging (``log_node``),
    so we do **not** call ``log_conversation`` here — only map
    ``answer_dict`` and ``response_kind`` back to Online State.
    """
    graph = _get_chat_graph()
    ctx = _unpack_context(config)

    # Use the context-resolved standalone query when available; fall
    # back to the raw message for backward compatibility.
    chat_message = state.get("standalone_query") or state.get("message", "")

    chat_input: dict[str, Any] = {
        "tenant_id": state.get("tenant_id", ""),
        "user_id": state.get("user_id", ""),
        "message": chat_message,
        "conversation_id": state.get("conversation_id", ""),
        "channel": state.get("channel", "local"),
        "agent_id": state.get("agent_id"),
        # Pass through Topic and precomputed routing fields
        "topic_id": state.get("topic_id"),
        "knowledge_policy": state.get("knowledge_policy"),
        "needs_retrieval": state.get("needs_retrieval"),
        "task_type": state.get("task_type"),
        "route_confidence": state.get("route_confidence"),
        "precomputed_route": True if state.get("knowledge_policy") else None,
        # Pass retained entities for topic key_entities_json update in log_node
        "retained_entities": state.get("retained_entities", []),
    }

    # Allow tests to inject a stub chat runner via runtime context
    chat_runner_override = ctx.get("chat_runner") if ctx else None
    runner = chat_runner_override or graph

    result = await runner.ainvoke(
        chat_input,
        config={"configurable": {"thread_id": f"chat:{chat_input['conversation_id']}"}},
        context=ctx,
    )

    return {
        "answer_dict": result.get("answer_dict") or result.get("final_answer", {}),
        "response_kind": "chat",
        "last_event_id": state.get("event_id"),
    }


# ====================================================================
# duplicate_node
# ====================================================================


def duplicate_node(state: OnlineConversationState) -> dict[str, Any]:
    """Handle duplicate events — does **not** update ``last_event_id``."""
    return {"response_kind": "duplicate"}


# ====================================================================
# clarification_response_node
# ====================================================================


async def clarification_response_node(
    state: OnlineConversationState,
) -> dict[str, Any]:
    """Format the clarification response for the user.

    The ``context_resolution_node`` has already set ``answer_dict`` with
    the clarification question.  This node simply ensures the correct
    response kind is set for downstream processing.
    """
    return {
        "response_kind": "clarify",
    }


# ====================================================================
# log_control_response_node
# ====================================================================


async def log_control_response_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Log a control response (clarification) and update ``last_event_id``.

    Control responses are non-chat system messages (e.g. clarification
    questions) that should still be persisted in the conversation log
    for audit purposes.
    """
    ctx = _unpack_context(config)
    db = ctx.get("db") if ctx else None

    if db is not None:
        answer_dict = state.get("answer_dict", {})
        try:
            await conversation_logger.log_conversation(
                db,
                tenant_id=state.get("tenant_id", ""),
                user_id=state.get("user_id", ""),
                channel=state.get("channel", "local"),
                agent_id=state.get("agent_id"),
                conversation_id=state.get("conversation_id", ""),
                message=state.get("original_message") or state.get("message", ""),
                task_type="clarify",
                answer_dict=answer_dict,
                path="clarify",
            )
        except Exception:
            logger.warning("Failed to log control response", exc_info=True)

    return {"last_event_id": state.get("event_id")}


# ====================================================================
# log_flow_output_node
# ====================================================================


async def log_flow_output_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Log guided-flow output and update ``last_event_id``.

    This runs after every guided-flow node (start / advance / cancel)
    and persists the turn to the conversation log.  Chat turns are
    logged by the Chat Graph itself.
    """
    ctx = _unpack_context(config)
    db = ctx.get("db") if ctx else None

    if db is not None:
        answer_dict = state.get("answer_dict", {})
        active_flow = state.get("active_flow")
        completed_flow = state.get("completed_flow")
        task_type = completed_flow or active_flow or "guided_flow"

        try:
            await conversation_logger.log_conversation(
                db,
                tenant_id=state.get("tenant_id", ""),
                user_id=state.get("user_id", ""),
                channel=state.get("channel", "local"),
                agent_id=state.get("agent_id"),
                conversation_id=state.get("conversation_id", ""),
                message=state.get("message", ""),
                task_type=task_type,
                answer_dict=answer_dict,
                path="guided_flow",
            )
        except Exception:
            logger.warning("Failed to log guided flow turn", exc_info=True)

    return {"last_event_id": state.get("event_id")}


# ====================================================================
# context_resolution_node  (was nodes/context_resolution.py)
# ====================================================================


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
    tenant_id: str | None = None,
    agent_id: str | None = None,
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
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
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
            tenant_id=scope["tenant_id"] or None,
            agent_id=scope["agent_id"] or None,
        )
        if result is not None:
            return result
        # If result is None (unknown resolution), fall through to
        # re-classify as ambiguous.

    # ── Step 3: Check / apply topic expiry ────────────────────────
    previous_topic_id: str | None = None
    if topic:
        was_closed = await manager.close_if_expired(db, topic, now=now)
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
        db=db,
        tenant_id=scope["tenant_id"] or None,
        agent_id=scope["agent_id"] or None,
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


# ====================================================================
# evidence_routing_node  (was nodes/evidence_routing.py)
# ====================================================================


async def evidence_routing_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Route intent and evidence policy for a context-resolved user message.

    This node is a **no-op** (returns ``{}``) when:

    - ``context_status`` is not ``"resolved"`` (e.g. clarify path).
    - Neither ``evidence_router_override`` nor *chat_model* is available.

    Args:
        state: Current graph state — must contain ``standalone_query``
            and ``context_status``.
        config: LangGraph ``RunnableConfig`` carrying runtime context.

    Returns:
        A partial state dict with evidence-routing fields, or ``{}``
        when skipped.
    """
    # Only run for resolved context
    if state.get("context_status") != "resolved":
        return {}

    ctx = _unpack_context(config)
    chat_model = ctx.get("chat_model") if ctx else None
    db = ctx.get("db") if ctx else None
    tenant_id = state.get("tenant_id") or None
    agent_id = state.get("agent_id") or None
    evidence_router_fn = (
        ctx.get("evidence_router_override") if ctx else None
    ) or route_intent_evidence

    standalone_query = state.get("standalone_query", "")

    # No model and no override — passthrough defaults
    if not standalone_query and not ctx:
        return {}

    # Call the evidence router
    decision = await evidence_router_fn(
        standalone_query=standalone_query,
        chat_model=chat_model,
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )

    # Map validated decision to state fields
    needs_retrieval = decision.knowledge_policy in ("required", "optional")

    return {
        "task_type": decision.intent,
        "route_confidence": decision.confidence,
        "knowledge_policy": decision.knowledge_policy,
        "knowledge_scope": decision.knowledge_scope,
        "retrieval_query": decision.retrieval_query,
        "needs_retrieval": needs_retrieval,
        "route_trace": decision.reason_code,
        "response_kind": "chat",
    }


async def direct_evidence_routing_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Intent classification for the ``direct_chat`` path (topic_routing off).

    When ``topic_routing_enabled`` is False, ``normalize_turn`` routes to
    ``direct_chat`` which bypasses ``context_resolution`` + ``evidence_routing``
    and goes straight to ``chat``. Without this node, ``knowledge_policy`` /
    ``needs_retrieval`` stay unset → ``select_retrieval_path`` returns
    ``"skip"`` → knowledge questions never retrieve, even product/policy
    queries that clearly need the knowledge base.

    This node runs the same ``route_intent_evidence`` service on the raw
    user message (no context rewriting, no clarify/topic management) so the
    chat subgraph receives a proper ``knowledge_policy`` and can fan out
    ontology + rag retrieval when the intent requires it. It is a no-op
    when no chat_model/override is available.

    Args:
        state: Current graph state — must contain ``message``.
        config: LangGraph ``RunnableConfig`` carrying runtime context.

    Returns:
        Partial state dict with evidence-routing fields, or ``{}`` skipped.
    """
    ctx = _unpack_context(config)
    chat_model = ctx.get("chat_model") if ctx else None
    db = ctx.get("db") if ctx else None
    tenant_id = state.get("tenant_id") or None
    agent_id = state.get("agent_id") or None
    evidence_router_fn = (
        ctx.get("evidence_router_override") if ctx else None
    ) or route_intent_evidence

    # Use the raw user message as the query (no context resolution on this path)
    message = state.get("message", "") or state.get("standalone_query", "")

    # No message, or no model and no override — passthrough defaults.
    # (route_intent_evidence needs a chat_model; without one we cannot classify.)
    override = ctx.get("evidence_router_override") if ctx else None
    if not message or (not override and not chat_model):
        return {}

    decision = await evidence_router_fn(
        standalone_query=message,
        chat_model=chat_model,
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )

    needs_retrieval = decision.knowledge_policy in ("required", "optional")

    return {
        "task_type": decision.intent,
        "route_confidence": decision.confidence,
        "knowledge_policy": decision.knowledge_policy,
        "knowledge_scope": decision.knowledge_scope,
        "retrieval_query": decision.retrieval_query,
        "needs_retrieval": needs_retrieval,
        "route_trace": decision.reason_code,
        "standalone_query": message,
        "original_message": message,
        "response_kind": "chat",
    }
