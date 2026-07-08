"""Node functions for the Online Conversation Graph.

All node functions live here so ``online/graph.py`` contains only the
graph builder.  This avoids circular imports (``chat_node`` needs the
cached subgraph factories, which in turn need the subgraph builders).

Quick index::

    _unpack_context              — extract runtime ctx from RunnableConfig
    _get_guided_flow_graph       — cached Guided Flow subgraph (no checkpointer)
    _get_chat_graph              — cached Chat subgraph (no checkpointer)

    normalize_turn_node          — set requested_flow + choose flow_action
    chat_node                    — invoke cached Chat Graph, map response fields
    duplicate_node               — handle duplicate events (no-op update)
    clarification_response_node  — format clarification response kind
    log_control_response_node    — persist control response to conversation log
    log_flow_output_node         — persist guided-flow output to conversation log

    _resolve_pending_turn        — helper: resolve a pending clarification on a topic
    context_resolution_node      — resolve context/topic for current message
    evidence_routing_node        — classify intent + evidence policy (resolved path)
    direct_evidence_routing_node — classify intent for direct_chat path (topic_routing off)
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
    TopicRestoreDecision,
    TopicScope,
)
from sales_agent.services.topic_manager import TopicManager, resolve_clarification
from sales_agent.services.topic_restore import (
    MAX_RESTORE_ATTEMPTS,
    resolve_topic_restore,
)

from sales_agent.core.config import get_settings
from sales_agent.scenarios import get_scenario_registry, match_scenario
from sales_agent.services.memory.commands import (
    MemoryCommand,
    apply_memory_command,
    detect_memory_command,
)
from sales_agent.services.memory.contracts import MemoryOperationResult, MemoryScope
from sales_agent.services.memory.profile_recall import retrieve_user_memory_context
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.transparency import detect_transparency_command, render_memory_transparency

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
    TopicRestoreDecision,
    TopicScope,
)
from sales_agent.services.topic_manager import TopicManager, resolve_clarification
from sales_agent.services.topic_restore import (
    MAX_RESTORE_ATTEMPTS,
    resolve_topic_restore,
)

logger = logging.getLogger(__name__)

# Default clarification question returned when the resolver cannot
# determine the user's intent.
_DEFAULT_CLARIFICATION_QUESTION = (
    "请问您想继续之前的话题，还是开始一个新话题？"
)

# =============================================================# Module-level subgraph caches
# =============================================================
_guided_flow_graph: CompiledStateGraph | None = None
_chat_graph: CompiledStateGraph | None = None


# =============================================================# Helpers
# =============================================================

def _unpack_context(config: RunnableConfig) -> dict[str, Any] | None:
    """Extract the runtime context dict from the LangGraph config."""
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime else None
    return ctx


# =============================================================# Subgraph factories (cached, no checkpointer — parent owns it)
# =============================================================

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


# =============================================================# normalize_turn
# =============================================================

def normalize_turn_node(state: OnlineConversationState) -> dict[str, Any]:
    """Set ``requested_flow`` on every turn and choose ``flow_action``.

    Routing priority (first match wins):

    1. **Duplicate** — ``event_id`` matches ``last_event_id``.
    2. **Reset** — ``reset_requested`` is ``True`` (Task 6).
    3. **Memory command** — ``long_term_memory_enabled`` & explicit memory command
       like "记住我负责华东区".  Explicit commands do NOT enter guided flow.
    4. **Start** — guided flows enabled & a requested-flow trigger.
    5. **Cancel** — guided flows enabled & active flow & cancel command.
    6. **Advance** — guided flows enabled & active flow.
    7. **Chat** — default.

    Reset must not fire on a duplicate — a redelivered reset event is a
    no-op, not a second state clear.
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
    elif state.get("reset_requested"):
        flow_action = "reset"
    elif state.get("user_profile_memory_enabled") and detect_transparency_command(message):
        flow_action = "profile_transparency"
    elif state.get("long_term_memory_enabled") and detect_memory_command(message):
        flow_action = "memory_command"
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


# =============================================================
# profile_recall_node (Task 5)
# =============================================================

async def profile_recall_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Recall user profile memory for the current conversation turn.

    Runs between ``evidence_routing`` and ``chat``.  Checks the
    ``user_profile_memory_enabled`` flag and ``context_status == "resolved"``
    before doing any work — otherwise returns ``{}``.

    On success returns the formatted memory context text plus trace metadata.
    On degradation (missing DB, exception) returns degraded-trace fields.
    """
    if not state.get("user_profile_memory_enabled"):
        return {}
    if state.get("context_status") != "resolved":
        return {}
    try:
        ctx = _unpack_context(config) or {}
        db = ctx.get("db")
        if db is None:
            return {
                "memory_degraded": True,
                "memory_degradation_reason": "missing_db",
                "memory_trace": {"degraded": True, "degradation_reason": "missing_db"},
            }
        settings = get_settings()
        result = await retrieve_user_memory_context(
            db=db,
            scope=MemoryScope(
                tenant_id=state.get("tenant_id", ""),
                agent_id=state.get("agent_id", ""),
                user_id=state.get("user_id", ""),
            ),
            standalone_query=state.get("standalone_query") or state.get("message", ""),
            task_type=state.get("task_type"),
            knowledge_policy=state.get("knowledge_policy"),
            max_items=settings.user_profile_memory.max_recall_items,
            max_chars=settings.user_profile_memory.max_recall_chars,
        )
        trace = result.trace.model_dump()
        return {
            "user_memory_context": result.context_text or None,
            "selected_memory_ids": trace.get("selected_memory_ids", []),
            "memory_trace": trace,
            "memory_degraded": bool(trace.get("degraded")),
            "memory_degradation_reason": trace.get("degradation_reason"),
            "profile_version": trace.get("profile_version"),
        }
    except Exception:
        logger.exception("profile_recall_node failed")
        return {
            "memory_degraded": True,
            "memory_degradation_reason": "exception",
            "memory_trace": {"degraded": True, "degradation_reason": "exception"},
        }


# =============================================================# chat_node
# =============================================================

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
        # User profile memory (Task 5)
        "user_memory_context": state.get("user_memory_context"),
        "selected_memory_ids": state.get("selected_memory_ids", []),
        "memory_trace": state.get("memory_trace", {}),
    }

    # Allow tests to inject a stub chat runner via runtime context
    chat_runner_override = ctx.get("chat_runner") if ctx else None
    runner = chat_runner_override or graph

    result = await runner.ainvoke(
        chat_input,
        config={"configurable": {"thread_id": f"chat:{chat_input['conversation_id']}"}},
        context=ctx,
    )

    # Build return dict — only include optional passthrough fields when the
    # Chat Graph actually set them, so we don't clobber upstream values
    # (e.g. task_type set by evidence_routing) with None.
    ret: dict[str, Any] = {
        "answer_dict": result.get("answer_dict") or result.get("final_answer", {}),
        "response_kind": "chat",
        "last_event_id": state.get("event_id"),
        # sources/risk_result/usage default to empty — safe to always set
        "sources": result.get("sources", []),
        "risk_result": result.get("risk_result", {}),
        "usage": result.get("usage", {}),
    }
    # Optional scalar fields: only overwrite when Chat Graph provided a value
    for key in ("route_confidence", "path", "task_type"):
        val = result.get(key)
        if val is not None:
            ret[key] = val
    return ret


# =============================================================# duplicate_node
# =============================================================

def duplicate_node(state: OnlineConversationState) -> dict[str, Any]:
    """Handle duplicate events — does **not** update ``last_event_id``."""
    return {"response_kind": "duplicate"}


# =============================================================# clarification_response_node
# =============================================================

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


# =============================================================# log_control_response_node
# =============================================================

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
            logger.exception("Failed to persist completed online turn")
            raise

    return {"last_event_id": state.get("event_id")}


# =============================================================# log_flow_output_node
# =============================================================

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
            logger.exception("Failed to persist completed online turn")
            raise

    return {"last_event_id": state.get("event_id")}


# =============================================================# scenario_coach
# =============================================================

async def scenario_coach_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Match the user message against preset sales scenarios.

    On a high-confidence match: set ``answer_dict`` (preset answer + manual
    source citation) and ``response_kind="scenario"`` so the graph
    short-circuits to the log node + END. On a miss: return ``last_event_id``
    and explicitly reset ``response_kind``/``answer_dict`` to ``None`` so a
    stale kind from a prior turn's hit cannot leak across turns (the graph
    checkpoint persists these routing-gating fields), leaving ``flow_action``
    intact so ``route_after_scenario`` resumes the normal downstream path.

    Fail-open: any matcher failure is treated as a miss (lesson #34).
    """
    ctx = _unpack_context(config)
    chat_model = ctx.get("chat_model") if ctx else None
    message = state.get("message", "")
    threshold = get_settings().scenario_coach.confidence_threshold

    matcher_fn = ctx.get("scenario_matcher_override") if ctx else None
    if matcher_fn is not None:
        decision = await matcher_fn(
            message=message, chat_model=chat_model, confidence_threshold=threshold
        )
    else:
        decision = await match_scenario(
            message=message, chat_model=chat_model, confidence_threshold=threshold
        )

    if decision.matched_question_id is None:
        logger.debug("scenario_coach: no match (reason=%s)", decision.reason_code)
        return {
            "last_event_id": state.get("event_id"),
            "response_kind": None,  # reset stale kind from a prior turn's hit
            "answer_dict": None,    # reset stale answer from a prior turn's hit
        }

    registry = get_scenario_registry()
    question = registry.get_question(decision.matched_question_id)
    if question is None:
        logger.warning("scenario_coach: matched id %s not in registry", decision.matched_question_id)
        return {
            "last_event_id": state.get("event_id"),
            "response_kind": None,  # reset stale kind from a prior turn's hit
            "answer_dict": None,    # reset stale answer from a prior turn's hit
        }

    answer_dict = {
        "summary": question.answer_summary,
        "sections": [
            {"title": s.title, "content": s.content} for s in question.answer_sections
        ],
        "sources": [
            {
                "title": registry.source_name,
                "display_title": registry.source_name,
                "source_type": "scenario_coach",
            }
        ],
    }
    logger.info(
        "scenario_coach: matched %s (confidence=%.2f)",
        decision.matched_question_id,
        decision.confidence,
    )
    return {
        "answer_dict": answer_dict,
        "response_kind": "scenario",
        "last_event_id": state.get("event_id"),
    }


async def log_scenario_response_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Persist a scenario-coach preset answer to the conversation log.

    Mirrors ``log_flow_output_node`` but with task_type/path="scenario".
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
                task_type="scenario",
                answer_dict=answer_dict,
                path="scenario",
            )
        except Exception:
            logger.warning("Failed to log scenario response", exc_info=True)

    return {"last_event_id": state.get("event_id")}


# =============================================================
# profile_transparency_node (Task 6)
# =============================================================

async def profile_transparency_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        text = "我现在暂时查不到长期记忆，请稍后再试。"
    else:
        repo = UserMemoryProfileRepository(db)
        profile = await repo.get_current_profile(
            MemoryScope(
                tenant_id=state.get("tenant_id", ""),
                agent_id=state.get("agent_id", ""),
                user_id=state.get("user_id", ""),
            )
        )
        text = render_memory_transparency(profile)
    return {
        "answer_dict": {
            "summary": text,
            "sections": [{"title": "长期记忆", "content": text}],
        },
        "response_kind": "profile_transparency",
        "last_event_id": state.get("event_id"),
    }


# =============================================================
# memory_command_node (Task 6)
# =============================================================

async def memory_command_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Handle an explicit memory command (remember / correct / forget).

    Detects the memory command from the user message, creates a
    ``MemoryScope`` from state identity fields, calls
    ``apply_memory_command()`` on the repository, and returns the
    response as ``answer_dict`` with ``response_kind="memory"``.

    On any database/application failure returns a clear error message
    and ``memory_status="failed"`` — the agent never crashes on a
    memory command.
    """
    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        return {
            "answer_dict": {
                "summary": "长期记忆暂时不可用，请稍后再试。",
                "sections": [],
            },
            "response_kind": "memory_error",
            "memory_operation": "noop",
            "memory_status": "failed",
            "memory_reason_code": "missing_db",
            "last_event_id": state.get("event_id"),
        }

    command = detect_memory_command(state.get("message", ""))
    if command is None:
        return {"flow_action": "chat"}

    scope = MemoryScope(
        tenant_id=state.get("tenant_id", ""),
        agent_id=state.get("agent_id", ""),
        user_id=state.get("user_id", ""),
    )
    repo = AtomicMemoryRepository(db)
    try:
        result = await apply_memory_command(
            repo=repo,
            scope=scope,
            command=command,
            conversation_id=state.get("conversation_id", ""),
            message_id=state.get("event_id") or "",
            now=ctx.get("now"),
        )
    except Exception:
        logger.exception("explicit memory command failed")
        result = MemoryOperationResult(
            operation=command.operation,
            status="failed",
            response_text="这条记忆没有保存成功，请稍后重试。",
            reason_code="write_failed",
        )

    return {
        "answer_dict": {
            "summary": result.response_text,
            "sections": [{"title": "长期记忆", "content": result.response_text}],
        },
        "response_kind": "memory",
        "memory_operation": result.operation,
        "memory_status": result.status,
        "memory_reason_code": result.reason_code,
        "memory_ids": result.memory_ids,
        "memory_candidate_count": result.candidate_count,
        "last_event_id": state.get("event_id"),
    }


# =============================================================
# enqueue_memory_candidate_node (Task 6)
# =============================================================

async def enqueue_memory_candidate_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Enqueue an inferred-memory outbox job after a successful chat turn.

    Runs **after** the assistant has answered so the answer text is never
    used as memory evidence.  Skips when:

    - ``long_term_memory_enabled`` is ``False``.
    - ``response_kind`` is one of ``duplicate``, ``memory``, ``clarification``,
      or ``flow`` (control paths that should not produce candidates).
    - ``event_id`` is missing.

    Fails open: any exception is caught, logged, and returns a
    ``memory_reason_code`` for observability without blocking the primary
    answer.
    """
    if not state.get("long_term_memory_enabled"):
        return {}
    if state.get("response_kind") in {"duplicate", "memory", "profile_transparency", "clarification", "flow"}:
        return {}
    event_id = state.get("event_id")
    if not event_id:
        return {}

    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        return {}

    try:
        scope = MemoryScope(
            tenant_id=state.get("tenant_id", ""),
            agent_id=state.get("agent_id", ""),
            user_id=state.get("user_id", ""),
        )
        repo = AtomicMemoryRepository(db)
        await repo.enqueue_inferred_job(
            scope,
            conversation_id=state.get("conversation_id", ""),
            event_id=event_id,
            payload={
                "tenant_id": scope.tenant_id,
                "agent_id": scope.agent_id,
                "user_id": scope.user_id,
                "conversation_id": state.get("conversation_id", ""),
                "message_id": event_id,
                "user_message": state.get("message", ""),
                "topic_summary": "",
                "verified_tool_facts": [],
            },
            now=ctx.get("now"),
        )
        return {"memory_reason_code": "inferred_outbox_enqueued"}
    except Exception:
        logger.warning("memory outbox enqueue failed", exc_info=True)
        return {"memory_reason_code": "inferred_outbox_enqueue_failed"}


# =============================================================
# reset_context_node (Task 6)
# =============================================================

# Reset confirmation emitted when a reset command carries no remaining
# message suffix. The user is told a new topic is open and prompted to
# state what they want to work on next.
_RESET_CONFIRMATION = "已开启新话题。你可以直接说当前要处理的销售问题。"


async def reset_context_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Close the active Topic + clear pending clarification + clear guided
    flow state, then either emit the reset confirmation (empty message) or
    hand the remaining message to ``context_resolution`` for a clean topic
    (force_new_topic).

    Routing (wired in ``graph.py``)::

        reset_context --empty message--> log_control_response --> END
        reset_context --remaining message--> context_resolution --> ...

    This node does NOT set ``last_event_id``: the empty-message path is
    logged by ``log_control_response_node`` (which sets it after a
    successful log), and the remaining-message path is logged by the Chat
    Graph's ``log_node``. A failed terminal node therefore cannot write
    ``last_event_id``.
    """
    ctx = _unpack_context(config)
    db = ctx.get("db") if ctx else None
    now = ctx.get("now") or datetime.now(timezone.utc)

    # Close the active Topic in the caller transaction. Uses the same
    # scope/db pattern as context_resolution_node.
    scope = {
        "tenant_id": state.get("tenant_id", ""),
        "agent_id": state.get("agent_id", ""),
        "user_id": state.get("user_id", ""),
        "channel": state.get("channel", ""),
    }

    if db is not None:
        manager: TopicManager = (
            ctx.get("topic_manager") if ctx and "topic_manager" in ctx
            else TopicManager()
        )
        topic = await manager.get_active_topic(db, **scope)
        if topic is not None:
            topic.status = "closed"
            topic.closed_at = now
            topic.pending_clarification_json = None
            await db.flush()

    # Common state: clear flow + force a clean topic for whatever follows.
    base: dict[str, Any] = {
        "active_flow": None,
        "flow_stage": None,
        "flow_payload": {},
        "force_new_topic": True,
        "turn_relation": "new",
        "pending_clarification_id": None,
        "clarification_state": None,
    }

    message = (state.get("message") or "").strip()
    if not message:
        # Empty suffix → emit the reset confirmation and route to
        # log_control_response (which sets last_event_id after logging).
        return {
            **base,
            "answer_dict": {"summary": _RESET_CONFIRMATION, "sections": []},
            "response_kind": "reset",
        }

    # Remaining suffix → context_resolution creates a clean topic and
    # routes through evidence_routing → chat. The message is already the
    # suffix (the processor strips the reset command before invoking).
    return base


# =============================================================# context_resolution_node  (was nodes/context_resolution.py)
# =============================================================

# ── helper: resolve a pending clarification on a topic ──────────────

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


# ── helpers: bounded topic restore ──────────────────────────────────


def _build_restore_clarify_result(
    *,
    anchor: ConversationTopic,
    candidates: list[ConversationTopic],
    message: str,
    event_id: str,
    supplemental: str | None,
) -> dict[str, Any]:
    """Build a ``clarify`` state update that lists ≤3 numbered candidates."""
    lines = []
    for idx, cand in enumerate(candidates[:3], start=1):
        summary = cand.summary or "(无摘要)"
        lines.append(f"{idx}. {summary}")
    question = "请问您想继续之前哪个话题？\n" + "\n".join(lines)
    return {
        "context_status": "clarify",
        "turn_relation": "ambiguous",
        "original_message": message,
        "standalone_query": supplemental or "",
        "topic_id": anchor.id,
        "pending_clarification_id": event_id,
        "clarification_state": "pending",
        "answer_dict": {"summary": question},
        "response_kind": "clarify",
    }


async def _execute_restore_decision(
    *,
    manager: TopicManager,
    db: Any,
    decision: TopicRestoreDecision,
    candidates: list[ConversationTopic],
    anchor: ConversationTopic | None,
    message: str,
    chat_model: Any,
    context_resolver_fn: Any,
    scope: TopicScope,
    conversation_id: str,
    event_id: str,
    now: datetime,
    tenant_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    """Execute a :class:`TopicRestoreDecision`.

    ``anchor`` is the existing active anchor when resolving a pending
    restore, or ``None`` on the initial turn. Closing the anchor is always
    flushed before restoring/creating a topic so the partial unique
    active-Topic index cannot be violated.
    """
    # ── restore a specific candidate ───────────────────────────────
    if decision.resolution == "restore":
        selected = next(
            (c for c in candidates if c.id == decision.selected_topic_id), None,
        )
        if selected is None:
            # Validated upstream — guard anyway by treating as ambiguous.
            logger.warning(
                "restore selected_topic_id %s not in candidates; ambiguous",
                decision.selected_topic_id,
            )
            decision = TopicRestoreDecision(
                resolution="ambiguous", reason_code="missing_candidate",
            )
        else:
            # Close + flush the anchor first so the unique active index is free.
            if anchor is not None and anchor.id != selected.id:
                anchor.status = "closed"
                anchor.closed_at = now
                anchor.pending_clarification_json = None
                await db.flush()
            restored = await manager.restore_topic(db, selected, now=now)
            supplemental = decision.supplemental_message
            if supplemental:
                # Run the suffix as a task on the restored topic.
                recent = await manager.load_recent_topic_messages(
                    db, scope=scope, conversation_id=conversation_id,
                    topic_id=restored.id,
                )
                ctx_dec = await context_resolver_fn(
                    message=supplemental, topic=restored,
                    recent_messages=recent, chat_model=chat_model,
                    db=db, tenant_id=tenant_id, agent_id=agent_id,
                )
                restored = await manager.apply_context_decision(
                    db, restored, ctx_dec, now=now,
                )
                return {
                    "context_status": "resolved",
                    "topic_id": restored.id,
                    "turn_relation": ctx_dec.turn_relation,
                    "standalone_query": ctx_dec.standalone_query or supplemental,
                    "retained_entities": ctx_dec.retained_entities,
                    "retracted_goals": ctx_dec.retracted_goals,
                    "original_message": message,
                    "pending_clarification_id": None,
                    "clarification_state": None,
                }
            # No supplemental task → control response (no chat reply).
            return {
                "context_status": "control",
                "response_kind": "topic_restored",
                "turn_relation": "continue",
                "topic_id": restored.id,
                "standalone_query": "",
                "original_message": message,
                "answer_dict": {
                    "summary": (
                        f"已继续之前的话题：{restored.summary}。"
                        "请告诉我接下来要处理什么。"
                    ),
                },
                "pending_clarification_id": None,
                "clarification_state": None,
            }

    # ── new: drop candidates, create a clean topic ─────────────────
    if decision.resolution == "new":
        if anchor is not None:
            anchor.status = "closed"
            anchor.closed_at = now
            anchor.pending_clarification_json = None
            await db.flush()
        task = decision.supplemental_message or message
        new_topic = await manager.create_topic(
            db,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            conversation_id=conversation_id,
            summary=task,
            current_goal=task,
            now=now,
        )
        if db is not None:
            await db.flush()  # populate new_topic.id
        return {
            "context_status": "resolved",
            "topic_id": new_topic.id,
            "turn_relation": "new",
            "standalone_query": task,
            "retained_entities": [],
            "retracted_goals": [],
            "original_message": message,
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    # ── ambiguous ──────────────────────────────────────────────────
    if anchor is not None:
        # Resolving an existing anchor: increment attempts, maybe fall back.
        anchor.clarification_attempts += 1
        if anchor.clarification_attempts >= MAX_RESTORE_ATTEMPTS:
            anchor.status = "closed"
            anchor.closed_at = now
            anchor.pending_clarification_json = None
            await db.flush()
            task = decision.supplemental_message or message
            new_topic = await manager.create_topic(
                db,
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                channel=scope.channel,
                conversation_id=conversation_id,
                summary=task,
                current_goal=task,
                now=now,
            )
            if db is not None:
                await db.flush()  # populate new_topic.id
            return {
                "context_status": "resolved",
                "topic_id": new_topic.id,
                "turn_relation": "new",
                "standalone_query": task,
                "retained_entities": [],
                "retracted_goals": [],
                "original_message": message,
                "pending_clarification_id": None,
                "clarification_state": None,
            }
        # Still ambiguous — re-ask using the same anchor.
        return _build_restore_clarify_result(
            anchor=anchor, candidates=candidates, message=message,
            event_id=event_id, supplemental=decision.supplemental_message,
        )

    # Initial ambiguous (no anchor yet): create a restore anchor.
    anchor = await manager.create_restore_anchor(
        db,
        scope=scope,
        conversation_id=conversation_id,
        event_id=event_id,
        original_message=message,
        candidates=candidates,
        now=now,
    )
    anchor.clarification_attempts = 1
    return _build_restore_clarify_result(
        anchor=anchor, candidates=candidates, message=message,
        event_id=event_id, supplemental=decision.supplemental_message,
    )


async def _resolve_restore_anchor(
    *,
    manager: TopicManager,
    db: Any,
    anchor: ConversationTopic,
    message: str,
    chat_model: Any,
    context_resolver_fn: Any,
    scope: TopicScope,
    conversation_id: str,
    event_id: str,
    now: datetime,
    tenant_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    """Resolve a pending ``topic_restore`` anchor on an active topic.

    Re-fetches the still-restorable candidates, asks the restore resolver,
    and executes the decision. Always returns a state-update dict.
    """
    try:
        pending = json.loads(anchor.pending_clarification_json or "{}")
    except (json.JSONDecodeError, TypeError):
        pending = {}

    # Duplicate event guard (same event that created the anchor).
    if pending.get("event_id") == event_id:
        return {"response_kind": "duplicate", "context_status": "clarify"}

    candidate_ids: list[str] = list(pending.get("candidate_topic_ids") or [])
    # Re-fetch still-restorable candidates (closed, within 24h) and keep only
    # those recorded in the anchor, preserving the stored order.
    restorable = await manager.find_restorable_topics(
        db,
        tenant_id=scope.tenant_id,
        agent_id=scope.agent_id,
        user_id=scope.user_id,
        channel=scope.channel,
        now=now,
    )
    by_id = {c.id: c for c in restorable}
    candidates = [by_id[cid] for cid in candidate_ids if cid in by_id]

    if not candidates:
        # All candidates fell out of the window — close anchor, start new.
        anchor.status = "closed"
        anchor.closed_at = now
        anchor.pending_clarification_json = None
        await db.flush()
        new_topic = await manager.create_topic(
            db,
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            conversation_id=conversation_id,
            summary=message,
            current_goal=message,
            now=now,
        )
        if db is not None:
            await db.flush()  # populate new_topic.id
        return {
            "context_status": "resolved",
            "topic_id": new_topic.id,
            "turn_relation": "new",
            "standalone_query": message,
            "retained_entities": [],
            "retracted_goals": [],
            "original_message": message,
            "pending_clarification_id": None,
            "clarification_state": None,
        }

    decision = await resolve_topic_restore(
        message=message,
        candidates=candidates,
        attempt_count=anchor.clarification_attempts,
        chat_model=chat_model,
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )

    return await _execute_restore_decision(
        manager=manager,
        db=db,
        decision=decision,
        candidates=candidates,
        anchor=anchor,
        message=message,
        chat_model=chat_model,
        context_resolver_fn=context_resolver_fn,
        scope=scope,
        conversation_id=conversation_id,
        event_id=event_id,
        now=now,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )


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

    # ── force_new_topic: reset cleared the slate; create a clean topic ──
    # reset_context_node already closed the active topic; we must NOT
    # restore the just-closed topic (or any other) — skip active/restorable
    # selection entirely and create a brand-new topic for the remaining
    # message. The flag is cleared in the output so it doesn't persist.
    if state.get("force_new_topic"):
        if db is None and "topic_manager" not in (ctx or {}):
            return {
                "context_status": "resolved",
                "standalone_query": message,
                "original_message": original_message,
                "turn_relation": "new",
                "force_new_topic": False,
                "pending_clarification_id": None,
                "clarification_state": None,
            }
        topic = await manager.create_topic(
            db,
            tenant_id=scope["tenant_id"],
            agent_id=scope["agent_id"],
            user_id=scope["user_id"],
            channel=scope["channel"],
            conversation_id=conversation_id,
            summary=message,
            current_goal=message,
            now=now,
        )
        if db is not None:
            await db.flush()
        return {
            "context_status": "resolved",
            "topic_id": topic.id,
            "turn_relation": "new",
            "standalone_query": message,
            "retained_entities": [],
            "retracted_goals": [],
            "original_message": original_message,
            "force_new_topic": False,
            "pending_clarification_id": None,
            "clarification_state": None,
        }

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

    topic_scope = TopicScope(
        tenant_id=scope["tenant_id"],
        agent_id=scope["agent_id"],
        user_id=scope["user_id"],
        channel=scope["channel"],
    )

    # ── Step 2: Resolve pending state on the active topic ─────────
    # A topic_restore anchor is resolved before a generic clarification.
    if topic and topic.pending_clarification_json:
        try:
            pending_kind = json.loads(topic.pending_clarification_json).get("kind")
        except (json.JSONDecodeError, TypeError):
            pending_kind = None

        if pending_kind == "topic_restore":
            return await _resolve_restore_anchor(
                manager=manager,
                db=db,
                anchor=topic,
                message=message,
                chat_model=chat_model,
                context_resolver_fn=context_resolver_fn,
                scope=topic_scope,
                conversation_id=conversation_id,
                event_id=event_id,
                now=now,
                tenant_id=scope["tenant_id"] or None,
                agent_id=scope["agent_id"] or None,
            )

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

    # ── Step 4: Restore path — no active topic, candidates exist ──
    # Resolve restore/new/ambiguous BEFORE generic context resolution.
    if topic is None:
        restorable = await manager.find_restorable_topics(
            db, **scope, now=now,
        )
        if restorable:
            restore_decision = await resolve_topic_restore(
                message=message,
                candidates=restorable,
                attempt_count=0,
                chat_model=chat_model,
                db=db,
                tenant_id=scope["tenant_id"] or None,
                agent_id=scope["agent_id"] or None,
            )
            return await _execute_restore_decision(
                manager=manager,
                db=db,
                decision=restore_decision,
                candidates=restorable,
                anchor=None,
                message=message,
                chat_model=chat_model,
                context_resolver_fn=context_resolver_fn,
                scope=topic_scope,
                conversation_id=conversation_id,
                event_id=event_id,
                now=now,
                tenant_id=scope["tenant_id"] or None,
                agent_id=scope["agent_id"] or None,
            )

    # ── Step 5: Generic context resolution on active/restored/new topic
    # Load the latest 6 user/assistant messages scoped to this topic so
    # the resolver has context. Messages from any other (closed) topic
    # are never loaded.
    recent_messages: list[dict[str, str]] = []
    if topic:
        recent_messages = await manager.load_recent_topic_messages(
            db,
            scope=topic_scope,
            conversation_id=conversation_id,
            topic_id=topic.id,
        )
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
        if db is not None:
            await db.flush()  # populate topic.id
    elif decision.turn_relation == "new":
        # Active topic + resolver 'new': close the old topic (flush so the
        # unique active-Topic index is freed) and create a fresh active one.
        previous_topic_id = topic.id
        topic.status = "closed"
        topic.closed_at = now
        await db.flush()
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
        if db is not None:
            await db.flush()  # populate topic.id
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


# =============================================================# evidence_routing_node  (was nodes/evidence_routing.py)
# =============================================================

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
