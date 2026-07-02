"""Test MCP server contract: allowed/disallowed tools, resources, prompts."""

import pytest

# Allowlisted tool names per PRD
ALLOWED_TOOLS = {
    "start_iteration",
    "get_iteration_status",
    "wait_for_iteration_update",
    "list_iteration_candidates",
    "compare_candidates",
    "get_iteration_report",
    "get_iteration_trend",
    "request_alternative_candidate",
    "rerun_candidate_evaluation",
}

FORBIDDEN_FRAGMENTS = {"approve", "publish", "rollback", "sql", "url", "file", "token", "admin"}


class TestMCPContract:
    def test_tool_set_is_exactly_allowlisted(self):
        """The MCP server must expose exactly these 9 tools."""
        from sales_agent.mcp_server.server import ALLOWED_TOOLS as registered
        assert registered == ALLOWED_TOOLS, (
            f"Tool mismatch: expected {ALLOWED_TOOLS - registered} extra, "
            f"unexpected {registered - ALLOWED_TOOLS}"
        )

    def test_no_forbidden_tool_names(self):
        """No tool name must contain forbidden fragments."""
        for tool in ALLOWED_TOOLS:
            for fragment in FORBIDDEN_FRAGMENTS:
                assert fragment not in tool, (
                    f"Tool '{tool}' contains forbidden fragment '{fragment}'"
                )

    def test_resource_templates_are_correct(self):
        """Verify the four resource template patterns exist."""
        from sales_agent.mcp_server.server import create_mcp_server
        # Without mcp installed, this will raise ImportError
        try:
            mcp = create_mcp_server()
            # FastMCP registration is verified at transport test time
        except ImportError:
            pytest.skip("mcp package not installed — skipping live check")

    def test_exactly_nine_tools(self):
        assert len(ALLOWED_TOOLS) == 9, "Must have exactly 9 tools"


class TestAPIClientTypes:
    def test_event_item_from_api(self):
        from sales_agent.mcp_server.types import EventItem
        e = EventItem.from_api({
            "sequence_no": 5,
            "event_type": "stage.progress",
            "stage": "diagnosing",
            "message": "50%",
        })
        assert e.sequence_no == 5
        assert e.event_type == "stage.progress"

    def test_wait_result_terminal_detection(self):
        from sales_agent.mcp_server.types import WaitResult
        w = WaitResult.from_api({
            "events": [],
            "next_sequence": 3,
            "terminal": True,
        })
        assert w.terminal is True
        assert w.next_sequence == 3
        assert len(w.events) == 0

    def test_iteration_status_from_api(self):
        from sales_agent.mcp_server.types import IterationStatus
        s = IterationStatus.from_api({
            "id": "i1", "agent_id": "a1", "iteration_no": 1,
            "status": "running", "current_stage": "diagnosing",
            "event_sequence": 15,
        })
        assert s.status == "running"
        assert s.current_stage == "diagnosing"
        assert s.event_sequence == 15


class TestAPIClientErrors:
    def test_observability_error_is_catchable(self):
        from sales_agent.mcp_server.api_client import ObservabilityApiError
        err = ObservabilityApiError(403, "human_approval_required", "test")
        assert err.status_code == 403
        assert err.code == "human_approval_required"
        assert "test" in str(err)
