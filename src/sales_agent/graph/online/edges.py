"""Conditional edge functions for the Online Conversation Graph."""

from __future__ import annotations

from sales_agent.graph.online.state import OnlineConversationState


def route_online_message(state: OnlineConversationState) -> str:
    """Return the destination node name based on ``flow_action``.

    Returns one of ``"duplicate"``, ``"reset"``, ``"start"``, ``"cancel"``,
    ``"advance"``, ``"chat"``, or ``"direct_chat"``.
    """
    return state.get("flow_action", "chat")


def route_reset_context(state: OnlineConversationState) -> str:
    """Return the next node after ``reset_context_node``.

    - ``"log_control_response"`` — empty message suffix: emit the reset
      confirmation, log it, and END.
    - ``"context_resolution"`` — remaining message suffix: create a clean
      topic and route through evidence_routing → chat.
    """
    message = (state.get("message") or "").strip()
    if message:
        return "context_resolution"
    return "log_control_response"


def route_context_resolution(state: OnlineConversationState) -> str:
    """Return the next node after context resolution.

    - ``"clarify"`` → the clarification response path
    - ``"resolved"`` → evidence routing then chat
    - ``"control"`` → log control response then END (e.g. topic_restored)
    """
    return state.get("context_status", "resolved")
