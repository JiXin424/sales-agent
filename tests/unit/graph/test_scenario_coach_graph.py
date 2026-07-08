"""Tests for scenario_coach graph wiring (state flag + input_state coverage)."""

from __future__ import annotations

from types import SimpleNamespace

from sales_agent.graph.online.edges import route_online_message


def test_route_online_message_disabled_passes_through():
    """When scenario_coach_enabled is False/absent, routing is unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": False}) == "chat"
    assert route_online_message({"flow_action": "direct_chat"}) == "direct_chat"
    assert route_online_message({"flow_action": "duplicate"}) == "duplicate"
    assert route_online_message({}) == "chat"  # default


def test_route_online_message_enabled_diverts_chat_paths():
    """When enabled, chat/direct_chat divert to scenario_coach; others unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": True}) == "scenario_coach"
    assert route_online_message({"flow_action": "direct_chat", "scenario_coach_enabled": True}) == "scenario_coach"
    # guided-flow / duplicate paths are NOT intercepted
    assert route_online_message({"flow_action": "start", "scenario_coach_enabled": True}) == "start"
    assert route_online_message({"flow_action": "duplicate", "scenario_coach_enabled": True}) == "duplicate"
