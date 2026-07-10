"""FastMCP server: tools, resources, and prompts for Claude Code.

Exposes the 9 allowlisted tools, 4 resource templates, and 2 prompts.
The server has no database access — it forwards authenticated calls to
the Observability API.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .api_client import ObservabilityApiClient, ObservabilityApiError
from .types import WaitResult, IterationStatus

logger = logging.getLogger(__name__)

# ── Client management ────────────────────────────────────────────────────────

# Global client singleton — one per server process.
_client: ObservabilityApiClient | None = None

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


def get_client() -> ObservabilityApiClient:
    global _client
    if _client is None:
        base = os.environ.get("SALES_AGENT_API_URL", "http://localhost:8000")
        token = os.environ.get("SALES_AGENT_MCP_TOKEN", "")
        _client = ObservabilityApiClient(base, token)
    return _client


# ── FastMCP wrapper ──────────────────────────────────────────────────────────


def create_mcp_server() -> Any:
    """Create and configure the FastMCP server.

    Returns the FastMCP instance ready for Streamable HTTP transport.
    The caller must configure host/port/transport separately.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "mcp package is required. Install with: pip install 'mcp>=1.27,<2'"
        )

    mcp = FastMCP(
        "sales-agent-iteration",
        instructions=(
            "Start, monitor, and analyze tenant-scoped knowledge optimization "
            "iterations. This server cannot approve, publish, or roll back "
            "releases. All operations are scoped to your credential's tenant "
            "and Agent allowlist."
        ),
    )

    # ── Tools ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def start_iteration(
        agent_id: str,
        fixed_suite_id: str,
        exploration_suite_id: str | None = None,
        max_candidates: int = 3,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a new knowledge optimization iteration.

        Args:
            agent_id: The target Agent ID (must be in your allowlist).
            fixed_suite_id: Fixed regression evaluation suite ID.
            exploration_suite_id: Optional exploration suite ID.
            max_candidates: Maximum number of candidates (1-10).
            idempotency_key: Unique key to prevent duplicate starts.
        """
        return await get_client().start_iteration(
            agent_id=agent_id,
            fixed_suite_id=fixed_suite_id,
            exploration_suite_id=exploration_suite_id,
            max_candidates=max_candidates,
            idempotency_key=idempotency_key,
        )

    @mcp.tool()
    async def get_iteration_status(
        agent_id: str, iteration_id: str,
    ) -> dict[str, Any]:
        """Get the current status of an optimization iteration.

        Returns id, status, current_stage, event_sequence, and
        final_report_id if available.
        """
        status = await get_client().get_status(agent_id, iteration_id)
        return {
            "id": status.id,
            "agent_id": status.agent_id,
            "iteration_no": status.iteration_no,
            "status": status.status,
            "current_stage": status.current_stage,
            "event_sequence": status.event_sequence,
            "final_report_id": status.final_report_id,
        }

    @mcp.tool()
    async def wait_for_iteration_update(
        agent_id: str,
        iteration_id: str,
        after_sequence: int = 0,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Wait for new events from a running iteration.

        Returns compact event summaries, next_sequence cursor for
        reconnect, and terminal flag. Call repeatedly with the returned
        next_sequence until terminal is True.

        Args:
            agent_id: The target Agent ID.
            iteration_id: The iteration to monitor.
            after_sequence: Resume from this event sequence number.
            timeout_seconds: Maximum wait time (1-30 seconds).
        """
        result = await get_client().wait_for_update(
            agent_id, iteration_id, after_sequence, timeout_seconds,
        )
        return {
            "events": [
                {
                    "seq": e.sequence_no,
                    "type": e.event_type,
                    "stage": e.stage,
                    "message": e.message,
                }
                for e in result.events
            ],
            "next_sequence": result.next_sequence,
            "terminal": result.terminal,
            "stage": result.stage,
            "suggested_action": (
                "iteration_complete" if result.terminal
                else "poll_again"
            ),
        }

    @mcp.tool()
    async def list_iteration_candidates(
        agent_id: str,
        iteration_id: str,
        cursor: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List optimization candidates for an iteration."""
        candidates = await get_client().list_candidates(agent_id, iteration_id)
        items = candidates if isinstance(candidates, list) else []
        return {"candidates": items[:limit], "total": len(items)}

    @mcp.tool()
    async def compare_candidates(
        agent_id: str,
        iteration_id: str,
        candidate_ids: list[str],
    ) -> dict[str, Any]:
        """Compare two or more candidates by fetching their reports."""
        results: list[dict[str, Any]] = []
        for cid in candidate_ids[:5]:
            try:
                data = await get_client().get_report(agent_id, iteration_id)
                results.append({"candidate_id": cid, "status": "fetched"})
            except ObservabilityApiError as e:
                results.append({"candidate_id": cid, "error": e.code})
        return {"comparisons": results}

    @mcp.tool()
    async def get_iteration_report(
        agent_id: str,
        iteration_id: str,
        report_id: str | None = None,
        format: str = "summary",
    ) -> dict[str, Any]:
        """Get the effect report for an iteration.

        Args:
            agent_id: The target Agent ID.
            iteration_id: The iteration.
            report_id: Specific report ID, or latest if omitted.
            format: summary, json, markdown, html, or csv.
        """
        report = await get_client().get_report(agent_id, iteration_id, report_id)
        if format == "summary":
            return {
                "report_id": report.get("id"),
                "report_type": report.get("report_type"),
                "recommendation": report.get("recommendation"),
                "effect_before": report.get("effect_index_before"),
                "effect_after": report.get("effect_index_after"),
                "effect_delta": report.get("effect_index_delta"),
                "hard_gates": report.get("hard_gates", {}).get("failed", []),
                "groups": len(report.get("groups", [])),
                "cases": len(report.get("cases", [])),
            }
        return report

    @mcp.tool()
    async def get_iteration_trend(
        agent_id: str, limit: int = 10,
    ) -> dict[str, Any]:
        """Get the latest completed final report trends."""
        return await get_client().get_trend(agent_id, limit)

    @mcp.tool()
    async def request_alternative_candidate(
        agent_id: str,
        iteration_id: str,
        diagnosis_id: str,
        constraints: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Request an alternative candidate with different constraints.

        Preserves the original diagnosis domain but applies new
        constraints. Does not change approval/publication state.

        Args:
            agent_id: The target Agent ID.
            iteration_id: The iteration.
            diagnosis_id: The failure diagnosis to generate against.
            constraints: Key-value constraints for the candidate generator.
            idempotency_key: Unique key to prevent duplicates.
        """
        client = get_client()
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return await client._request(
            "POST",
            f"/agents/{agent_id}/optimization/iterations/{iteration_id}/alternative-candidate",
            body={
                "diagnosis_id": diagnosis_id,
                "constraints": constraints or {},
            },
            headers=headers,
        )

    @mcp.tool()
    async def rerun_candidate_evaluation(
        agent_id: str,
        iteration_id: str,
        candidate_id: str,
        suite_type: str = "fixed",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Rerun the evaluation suite for a candidate.

        Args:
            agent_id: The target Agent ID.
            iteration_id: The iteration.
            candidate_id: The candidate to re-evaluate.
            suite_type: "targeted" or "fixed".
            idempotency_key: Unique key to prevent duplicate eval runs.
        """
        client = get_client()
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return await client._request(
            "POST",
            f"/agents/{agent_id}/optimization/candidates/{candidate_id}/rerun-evaluation",
            body={"suite_type": suite_type},
            headers=headers,
        )

    # ── Resources ────────────────────────────────────────────────────────

    @mcp.resource("iteration://{agent_id}/{iteration_id}/status")
    async def iteration_status_resource(agent_id: str, iteration_id: str) -> str:
        """Current status of one iteration."""
        status = await get_client().get_status(agent_id, iteration_id)
        return (
            f"Iteration {status.iteration_no}: {status.status} "
            f"(stage: {status.current_stage or 'N/A'}, "
            f"events: {status.event_sequence})"
        )

    @mcp.resource("iteration://{agent_id}/{iteration_id}/report")
    async def iteration_report_resource(agent_id: str, iteration_id: str) -> str:
        """Markdown report for one iteration."""
        from .api_client import ObservabilityApiError
        try:
            report = await get_client().get_report(agent_id, iteration_id)
            return (
                f"Report: {report.get('report_type')}, "
                f"Recommendation: {report.get('recommendation')}, "
                f"Effect: {report.get('effect_index_delta'):+.1f}"
            )
        except ObservabilityApiError:
            return "Report not available"

    @mcp.resource("iteration://{agent_id}/{iteration_id}/candidates")
    async def iteration_candidates_resource(agent_id: str, iteration_id: str) -> str:
        """Candidate list for one iteration."""
        candidates = await get_client().list_candidates(agent_id, iteration_id)
        items = candidates if isinstance(candidates, list) else []
        return f"{len(items)} candidate(s)"

    @mcp.resource("iteration://{agent_id}/trend/latest-10")
    async def iteration_trend_resource(agent_id: str) -> str:
        """Latest 10 final report trends."""
        trend = await get_client().get_trend(agent_id)
        items = trend.get("trends", [])
        lines = [f"{t.get('recommendation', 'N/A'):20} Δ={t.get('effect_index_delta', 0):+.1f}" for t in items[:10]]
        return "\n".join(lines) if lines else "No trend data"

    # ── Prompts ──────────────────────────────────────────────────────────

    @mcp.prompt()
    async def monitor_iteration(agent_id: str, iteration_id: str) -> str:
        """Monitor an iteration from start to finish.

        Loop with wait_for_iteration_update using the returned cursor until
        terminal. When complete, fetch the final report and summarize.
        Do not call approve, publish, or rollback — those require a human.
        """
        return (
            f"Monitor iteration {iteration_id} for agent {agent_id}.\n\n"
            "1. Call get_iteration_status to check the current state.\n"
            "2. Loop: call wait_for_iteration_update with the returned "
            "next_sequence.\n"
            "3. If terminal, fetch the report with get_iteration_report.\n"
            "4. Surface hard-gate failures or regressions immediately.\n"
            "5. NEVER call approve, publish, or rollback — those are "
            "human-only actions."
        )

    @mcp.prompt()
    async def analyze_iteration_report(agent_id: str, iteration_id: str) -> str:
        """Analyze a completed iteration's effect report.

        Review the composite effect index, each metric group, hard gates,
        and per-case regressions. The recommendation precedence is:
        rollback_recommended > do_not_publish > improved > neutral > regressed.
        """
        return (
            f"Analyze the effect report for iteration {iteration_id} on "
            f"agent {agent_id}.\n\n"
            "1. Call get_iteration_report to fetch the full report.\n"
            "2. Check hard_gates — any failure overrides the composite.\n"
            "3. Review each metric group for regressions.\n"
            "4. Flag cases classified as 'regressed' or 'error'.\n"
            "5. Summarize the recommendation and whether action is needed."
        )

    return mcp
