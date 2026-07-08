"""Conditional edge functions for the Online Conversation Graph."""

from __future__ import annotations

from sales_agent.graph.online.state import OnlineConversationState


def route_online_message(state: OnlineConversationState) -> str:
    """Return the destination node name based on ``flow_action``.

    When ``scenario_coach_enabled`` is set, ``chat`` and ``direct_chat``
    divert to the ``scenario_coach`` node first (the original flow_action
    is preserved in state so ``route_after_scenario`` can resume the
    correct downstream path on a miss). Returns one of ``"duplicate"``,
    ``"start"``, ``"cancel"``, ``"advance"``, ``"chat"``, ``"direct_chat"``,
    or ``"scenario_coach"``.
    """
    flow_action = state.get("flow_action", "chat")
    if state.get("scenario_coach_enabled", False) and flow_action in ("chat", "direct_chat"):
        return "scenario_coach"
    return flow_action


def route_context_resolution(state: OnlineConversationState) -> str:
    """Return the next node after context resolution.

    - ``"clarify"`` → the clarification response path
    - ``"resolved"`` → evidence routing then chat
    """
    return state.get("context_status", "resolved")
