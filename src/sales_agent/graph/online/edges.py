"""Conditional edge functions for the Online Conversation Graph."""

from __future__ import annotations

from sales_agent.graph.online.state import OnlineConversationState


def route_online_message(state: OnlineConversationState) -> str:
    """Return the destination node name based on ``flow_action``.

When ``scenario_coach_enabled`` is set, ``chat`` and ``direct_chat``
    divert to the ``scenario_coach`` node first (the original flow_action
    is preserved in state so ``route_after_scenario`` can resume the
    correct downstream path on a miss). Returns one of ``"duplicate"``,
    ``"reset"``, ``"profile_transparency"``, ``"memory_command"``, ``"start"``,
    ``"cancel"``, ``"advance"``, ``"chat"``,
    ``"direct_chat"``, or ``"scenario_coach"``.
    """
    flow_action = state.get("flow_action", "chat")
    if state.get("scenario_coach_enabled", False) and flow_action in ("chat", "direct_chat"):
        return "scenario_coach"
    return flow_action


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


def route_after_scenario(state: OnlineConversationState) -> str:
    """Return the next node after the scenario_coach node.

    - ``"scenario_hit"`` → log_scenario_response → END
    - otherwise resume the original path: ``"direct_chat"`` →
      direct_evidence_routing, ``"chat"`` → context_resolution.
    """
    if state.get("response_kind") == "scenario":
        return "scenario_hit"
    if state.get("flow_action") == "direct_chat":
        return "direct_chat"
    return "chat"


def route_after_sales_action(state: OnlineConversationState) -> str:
    """Return the next node after ``sales_action_command_node``.

    - ``"sales_action_end"`` → END — the turn was handled as a sales action
      (created/clarified/listed/…).
    - ``"resume_chat"`` → context_resolution — the service declined
      (``response_kind == "chat"``); resume the normal chat path so the user
      still gets a real answer.
    """
    if state.get("response_kind") == "sales_action":
        return "sales_action_end"
    return "resume_chat"
