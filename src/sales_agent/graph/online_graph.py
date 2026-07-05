"""Unified online conversation graph — entry point for all message processing.

Routes every incoming message to either:

- The **Guided Flow** subgraph (start / advance / cancel)
- The **Chat** graph (existing pipeline)
- A **duplicate** handler

Routing priority (highest first):

1. Duplicate event (same ``event_id`` as ``last_event_id``)
2. ``guided_flows_enabled`` & a ``requested_flow`` trigger  →  ``"start"``
3. ``guided_flows_enabled`` & ``active_flow`` & cancel command →  ``"cancel"``
4. ``guided_flows_enabled`` & ``active_flow`` →  ``"advance"``
5. Otherwise →  ``"chat"``
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
from sales_agent.graph.guided_flow.triggers import is_cancel_command, resolve_requested_flow
from sales_agent.graph.online_state import OnlineConversationState
from sales_agent.services import conversation_logger

logger = logging.getLogger(__name__)


# ====================================================================
# Module-level caches
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
# Nodes
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

    return {
        "requested_flow": requested_flow,
        "flow_action": flow_action,
    }


def route_online_message(state: OnlineConversationState) -> str:
    """Return the destination node name based on ``flow_action``.

    Returns one of ``"duplicate"``, ``"start"``, ``"cancel"``,
    ``"advance"``, or ``"chat"``.
    """
    return state.get("flow_action", "chat")


async def chat_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Invoke the cached Chat Graph and map stable response fields.

    The Chat Graph handles its own conversation logging (``log_node``),
    so we do **not** call ``log_conversation`` here — only map
    ``answer_dict`` and ``response_kind`` back to Online State.
    """
    graph = _get_chat_graph()
    ctx = _unpack_context(config)

    chat_input: dict[str, Any] = {
        "tenant_id": state.get("tenant_id", ""),
        "user_id": state.get("user_id", ""),
        "message": state.get("message", ""),
        "conversation_id": state.get("conversation_id", ""),
        "channel": state.get("channel", "local"),
        "agent_id": state.get("agent_id"),
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


def duplicate_node(state: OnlineConversationState) -> dict[str, Any]:
    """Handle duplicate events — does **not** update ``last_event_id``."""
    return {"response_kind": "duplicate"}


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
# Graph builder
# ====================================================================


def build_online_graph() -> StateGraph:
    """Build the unified online conversation graph.

    Structure::

        START  →  normalize_turn  →  [conditional]
                         │
            ┌────────────┼──────────────┐
            v            v              v
        duplicate    guided_flow      chat
                         │
                         v
                   log_flow_output
                         │
            └────────────┴──────────────┘
                         v
                        END

    Returns:
        An uncompiled :class:`StateGraph`.  The caller must call
        ``.compile(checkpointer=…)`` before invoking.
    """
    builder = StateGraph(OnlineConversationState)

    # ── Nodes ──────────────────────────────────────────────────────
    builder.add_node("normalize_turn", normalize_turn_node)
    builder.add_node("guided_flow", _get_guided_flow_graph())
    builder.add_node("chat", chat_node)
    builder.add_node("duplicate", duplicate_node)
    builder.add_node("log_flow_output", log_flow_output_node)

    # ── Edges ──────────────────────────────────────────────────────
    builder.add_edge(START, "normalize_turn")

    builder.add_conditional_edges(
        "normalize_turn",
        route_online_message,
        {
            "duplicate": "duplicate",
            "start": "guided_flow",
            "cancel": "guided_flow",
            "advance": "guided_flow",
            "chat": "chat",
        },
    )

    builder.add_edge("guided_flow", "log_flow_output")
    builder.add_edge("log_flow_output", END)
    builder.add_edge("chat", END)
    builder.add_edge("duplicate", END)

    return builder


__all__ = [
    "build_online_graph",
    "chat_node",
    "duplicate_node",
    "log_flow_output_node",
    "normalize_turn_node",
    "route_online_message",
]
