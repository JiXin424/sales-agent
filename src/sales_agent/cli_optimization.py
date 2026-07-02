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
):
    """Watch an iteration (poll status)."""
    result = _fetch("GET", f"/agents/{agent_id}/optimization/iterations/{iteration_id}")
    typer.echo(f"Status: {result.get('status')}")
    typer.echo(f"Iteration #{result.get('iteration_no')}")


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
