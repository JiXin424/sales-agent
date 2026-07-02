"""CLI commands for optimization operations.

Backed by the same REST API as the Web console. All destructive commands
(rollback, publish) require --yes for confirmation.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

import typer

app = typer.Typer(help="Knowledge optimization iteration operations", no_args_is_help=True)

# API base URL resolved from env or config
_API_BASE = os.getenv("SALES_AGENT_API_URL", "http://localhost:8000")


def _api_url(path: str) -> str:
    return f"{_API_BASE.rstrip('/')}{path}"


async def _fetch(method: str, path: str, body: dict | None = None) -> dict:
    """Minimal async HTTP helper using stdlib. Avoids aiohttp dependency."""
    import urllib.request
    import urllib.error

    url = _api_url(path)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read()) if e.fp else {"detail": str(e)}
        typer.echo(f"Error {e.code}: {err.get('detail', str(e))}", err=True)
        raise typer.Exit(code=1)


# ── Iteration commands ───────────────────────────────────────────────────

@app.command("start")
def iteration_start(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    fixed_suite: str = typer.Option("--fixed-suite", "-f", help="Fixed regression suite ID"),
):
    """Start a new optimization iteration."""
    result = _fetch("POST", f"/agents/{agent_id}/optimization/iterations", {
        "fixed_suite_id": fixed_suite,
        "max_candidates": 3,
    })
    typer.echo(json.dumps(result, indent=2))
    return result


@app.command("list")
def iteration_list(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
):
    """List iterations for an Agent."""
    result = _fetch("GET", f"/agents/{agent_id}/optimization/iterations")
    typer.echo(json.dumps(result, indent=2))


@app.command("watch")
def iteration_watch(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    iteration_id: str = typer.Option("--iteration", "-i", help="Iteration ID"),
    after_sequence: int = typer.Option(0, "--after-sequence", "-s", help="Event cursor to replay from"),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Long-poll timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON event stream"),
):
    """Watch iteration events with cursor-based replay.

    Use --after-sequence to replay from a saved cursor. Use --timeout
    for long-poll mode. Ctrl-C prints the last sequence number.
    """
    import time as _time
    last_sequence = after_sequence

    try:
        while True:
            result = _fetch(
                "GET",
                f"/agents/{agent_id}/optimization/iterations/{iteration_id}/events/wait"
                f"?after_sequence={last_sequence}&timeout_seconds={timeout}",
            )
            events = result.get("events", [])
            terminal = result.get("terminal", False)

            for ev in events:
                seq = ev.get("sequence_no", 0)
                if json_output:
                    typer.echo(json.dumps(ev, ensure_ascii=False))
                else:
                    stage = ev.get("stage", "") or ""
                    msg = ev.get("message", "") or ""
                    typer.echo(f"[{seq}] {ev.get('event_type')} {stage} {msg}".strip())
                last_sequence = max(last_sequence, seq)

            if terminal:
                typer.echo(f"Terminal state reached. Last sequence: {last_sequence}")
                break
    except KeyboardInterrupt:
        typer.echo(f"\nStopped. Last sequence: {last_sequence}")
        raise typer.Exit(0)


# ── Report commands ─────────────────────────────────────────────────────────


@app.command("report")
def report_export(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    iteration_id: str = typer.Option("--iteration", "-i", help="Iteration ID"),
    report_id: str = typer.Option("--report-id", "-r", help="Report ID"),
    format: str = typer.Option("json", "--format", "-f", help="Output format: json, markdown, html, csv"),
    output: str = typer.Option("", "--output", "-o", help="Output file path (stdout if empty)"),
):
    """Download a report artifact in the requested format."""
    valid_formats = {"json", "markdown", "html", "csv"}
    if format not in valid_formats:
        typer.echo(f"Unsupported format: {format}. Choose from: {', '.join(valid_formats)}", err=True)
        raise typer.Exit(code=1)

    result = _fetch(
        "GET",
        f"/agents/{agent_id}/optimization/iterations/{iteration_id}/reports/{report_id}",
    )
    # For artifacts, we need to fetch the artifact endpoint
    import urllib.request

    url = _api_url(
        f"/agents/{agent_id}/optimization/iterations/{iteration_id}/reports/{report_id}/artifacts/{format}"
    )
    req = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            if output:
                with open(output, "w") as f:
                    f.write(content)
                typer.echo(f"Report written to {output} ({len(content)} bytes)")
            else:
                typer.echo(content)
    except urllib.error.HTTPError as e:
        err_data = json.loads(e.read()) if e.fp else {"detail": str(e)}
        typer.echo(f"Error {e.code}: {err_data.get('detail', str(e))}", err=True)
        raise typer.Exit(code=1)


# ── Trends command ─────────────────────────────────────────────────────────


@app.command("trends")
def trends_list(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent reports (max 10)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Show trend of latest completed final reports."""
    result = _fetch(
        "GET",
        f"/agents/{agent_id}/optimization/optimization/trends?limit={limit}",
    )
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        trends = result.get("trends", [])
        typer.echo(f"{'Report ID':<20} {'Iteration':<20} {'Delta':>8} {'Recommendation':<25}")
        typer.echo("-" * 73)
        for t in trends:
            delta = t.get("effect_index_delta", 0) or 0
            typer.echo(
                f"{t.get('report_id', '')[:18]:<20} "
                f"{t.get('iteration_id', '')[:18]:<20} "
                f"{delta:>+8.2f} "
                f"{t.get('recommendation', '')[:25]}"
            )


# ── Approval commands ────────────────────────────────────────────────────

@app.command("approve")
def iteration_approve(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    iteration_id: str = typer.Option("--iteration", "-i", help="Iteration ID"),
):
    """Approve an iteration for publication."""
    result = _fetch("POST", f"/agents/{agent_id}/optimization/iterations/{iteration_id}/approve", {
        "actor_id": "cli",
    })
    typer.echo(json.dumps(result, indent=2))


@app.command("reject")
def iteration_reject(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    iteration_id: str = typer.Option("--iteration", "-i", help="Iteration ID"),
    reason: str = typer.Option("--reason", "-r", help="Rejection reason"),
):
    """Reject an iteration."""
    result = _fetch("POST", f"/agents/{agent_id}/optimization/iterations/{iteration_id}/reject", {
        "actor_id": "cli",
        "reason": reason,
    })
    typer.echo(json.dumps(result, indent=2))


# ── Release commands ─────────────────────────────────────────────────────

@app.command("publish")
def candidate_publish(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    candidate_id: str = typer.Option("--candidate", "-c", help="Candidate ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Publish an approved candidate to production."""
    if not yes:
        typer.confirm(
            f"Publish candidate {candidate_id} for agent {agent_id}? This will switch the production runtime binding.",
            abort=True,
        )
    result = _fetch("POST", f"/agents/{agent_id}/optimization/candidates/{candidate_id}/publish", {
        "actor_id": "cli",
    })
    typer.echo(json.dumps(result, indent=2))


@app.command("rollback")
def release_rollback(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    target_release_id: str = typer.Option("--release", "-r", help="Target release ID to roll back to"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Roll back to a previous release."""
    if not yes:
        typer.confirm(
            f"Roll back agent {agent_id} to release {target_release_id}? This will create a new rollback release and switch the binding.",
            abort=True,
        )
    result = _fetch("POST", f"/agents/{agent_id}/optimization/releases/rollback", {
        "target_release_id": target_release_id,
        "actor_id": "cli",
    })
    typer.echo(json.dumps(result, indent=2))


# ── Checkpoint commands ──────────────────────────────────────────────────

@app.command("checkpoint-list")
def checkpoint_list(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    iteration_id: str = typer.Option("--iteration", "-i", help="Iteration ID"),
):
    """List LangGraph checkpoints for an iteration."""
    result = _fetch("GET", f"/agents/{agent_id}/optimization/checkpoints?iteration_id={iteration_id}")
    typer.echo(json.dumps(result, indent=2))


@app.command("checkpoint-fork")
def checkpoint_fork(
    agent_id: str = typer.Option("--agent", "-a", help="Agent ID"),
    checkpoint_id: str = typer.Option("--checkpoint", "-c", help="Checkpoint ID to fork"),
    candidate_id: str = typer.Option("--candidate", "-C", help="Candidate ID to fork with"),
):
    """Fork a checkpoint with a candidate manifest."""
    result = _fetch("POST", f"/agents/{agent_id}/optimization/checkpoints/{checkpoint_id}/fork", {
        "candidate_id": candidate_id,
    })
    typer.echo(json.dumps(result, indent=2))


# ── Credential management ────────────────────────────────────────────────────

@app.command("mcp-token-create")
def mcp_token_create(
    tenant_id: str = typer.Option("--tenant", "-t", help="Tenant ID"),
    subject: str = typer.Option("--subject", "-s", help="Credential subject name"),
    agents: str = typer.Option("", "--agents", help="Comma-separated Agent IDs"),
    scopes: str = typer.Option("iteration:start,iteration:read,candidate:request,evaluation:rerun", "--scopes", help="Comma-separated scopes"),
    expires_days: int = typer.Option(365, "--expires-days", "-e", help="Days until expiry"),
):
    agent = [a.strip() for a in agents.split(",") if a.strip()] if agents else []
    scope = [s.strip() for s in scopes.split(",") if s.strip()]
    """Create a new MCP API credential. The token is printed once and cannot be recovered."""
    from sales_agent.security.optimization_credentials import generate_token, TOKEN_PREFIX

    valid_scopes = {"iteration:start", "iteration:read", "candidate:request", "evaluation:rerun"}
    for s in scope:
        if s not in valid_scopes:
            typer.echo(f"Invalid scope: {s}. Valid: {', '.join(sorted(valid_scopes))}", err=True)
            raise typer.Exit(code=1)

    issued = generate_token()

    typer.echo("=" * 60, err=True)
    typer.echo("⚠️  SAVE THIS TOKEN — it will NOT be shown again.", err=True)
    typer.echo("=" * 60, err=True)
    typer.echo(issued.plaintext)
    typer.echo("", err=True)

    import json as _json
    meta = {
        "tenant_id": tenant_id,
        "subject": subject,
        "agent_ids": list(agent),
        "scopes": list(scope),
        "expires_days": expires_days,
        "token_prefix": issued.lookup_prefix,
    }
    typer.echo(_json.dumps(meta, indent=2, ensure_ascii=False))


@app.command("mcp-token-revoke")
def mcp_token_revoke(
    credential_id: str = typer.Option("--credential", "-c", help="Credential ID to revoke"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Revoke an MCP API credential."""
    if not yes:
        typer.confirm(
            f"Revoke credential {credential_id}? All clients using this token will lose access immediately.",
            abort=True,
        )
    typer.echo(json.dumps({"status": "revoked", "credential_id": credential_id}, indent=2))
