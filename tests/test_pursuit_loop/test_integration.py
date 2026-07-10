"""Integration tests for pursuit loop graph wiring.

Tests that normalize_turn_node correctly routes to/from observe based on
pursuit_loop_enabled and pending_observe_action_id state flags.
"""

import pytest

from sales_agent.graph.online.nodes import normalize_turn_node


@pytest.mark.asyncio
async def test_pursuit_loop_disabled_does_not_route_to_observe():
    """When pursuit_loop_enabled=False, message with pending_observe_action_id still falls through to chat."""
    state = {
        "message": "约到了",
        "event_id": "evt-1",
        "pursuit_loop_enabled": False,
        "sales_actions_enabled": True,
        "pending_observe_action_id": "act-1",
    }
    result = normalize_turn_node(state)
    assert result["flow_action"] != "sales_action_observe", (
        "Expected flow_action != sales_action_observe when pursuit_loop_enabled=False"
    )


@pytest.mark.asyncio
async def test_pursuit_loop_enabled_routes_to_observe():
    """When pursuit_loop_enabled=True and pending_observe_action_id is set, route to observe."""
    state = {
        "message": "约到了",
        "event_id": "evt-2",
        "pursuit_loop_enabled": True,
        "sales_actions_enabled": True,
        "pending_observe_action_id": "act-1",
    }
    result = normalize_turn_node(state)
    assert result["flow_action"] == "sales_action_observe", (
        "Expected flow_action == sales_action_observe when pursuit_loop_enabled=True "
        "and pending_observe_action_id is set"
    )


@pytest.mark.asyncio
async def test_pursuit_loop_enabled_no_pending_routes_to_chat():
    """When pursuit_loop_enabled=True but no pending_observe_action_id, route to chat."""
    state = {
        "message": "今天天气不错",
        "event_id": "evt-3",
        "pursuit_loop_enabled": True,
        "sales_actions_enabled": True,
        "pending_observe_action_id": None,
    }
    result = normalize_turn_node(state)
    assert result["flow_action"] in ("chat", "direct_chat"), (
        "Expected flow_action to be chat or direct_chat when no pending_observe_action_id"
    )
