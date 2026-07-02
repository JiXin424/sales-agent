"""Optimization API routes: Agent-scoped iteration lifecycle.

All routes enforce Agent tenant ownership. Thread IDs use the
``kbopt:{tenant}:{iteration}:{candidate}:{run}`` prefix.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api import optimization_schemas as schemas
from sales_agent.models.agent import Agent
from sales_agent.models.optimization import (
    OptimizationIteration,
    OptimizationCandidate,
    IterationGraphCheckpoint,
)
from sales_agent.models.runtime_release import AgentRuntimeBinding
from sales_agent.models.base import generate_id, utcnow
from sales_agent.services.release_service import ReleaseService
from sales_agent.services.release_types import StaleRuntimeBinding, ReleaseNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/optimization", tags=["optimization"])


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_agent(db: AsyncSession, agent_id: str) -> Agent:
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _get_iteration(
    db: AsyncSession, tenant_id: str, agent_id: str, iteration_id: str,
) -> OptimizationIteration:
    iteration = await db.scalar(
        select(OptimizationIteration).where(
            OptimizationIteration.id == iteration_id,
            OptimizationIteration.tenant_id == tenant_id,
            OptimizationIteration.agent_id == agent_id,
        )
    )
    if iteration is None:
        raise HTTPException(status_code=404, detail="Iteration not found")
    return iteration


# ── Iterations ───────────────────────────────────────────────────────────

@router.post("/iterations", response_model=schemas.IterationResponse)
async def start_iteration(
    agent_id: str,
    body: schemas.StartIterationRequest,
    db: DbSession,
):
    """Start a new optimization iteration for an Agent."""
    agent = await _get_agent(db, agent_id)

    # Get next iteration_no
    max_no = await db.scalar(
        select(func.max(OptimizationIteration.iteration_no)).where(
            OptimizationIteration.tenant_id == agent.tenant_id,
            OptimizationIteration.agent_id == agent_id,
        )
    )
    iteration_no = (max_no or 0) + 1

    iteration = OptimizationIteration(
        id=generate_id(),
        tenant_id=agent.tenant_id,
        agent_id=agent_id,
        iteration_no=iteration_no,
        status="running",
        fixed_suite_id=body.fixed_suite_id,
        exploration_suite_id=body.exploration_suite_id,
        max_candidates=body.max_candidates,
        max_consecutive_failures=body.max_consecutive_failures,
        allowed_change_types_json=json.dumps(body.allowed_change_types),
        created_by="api",
    )
    db.add(iteration)
    await db.flush()

    return schemas.IterationResponse(
        id=iteration.id,
        tenant_id=iteration.tenant_id,
        agent_id=iteration.agent_id,
        iteration_no=iteration.iteration_no,
        status=iteration.status,
        created_at=iteration.created_at,
    )


@router.get("/iterations", response_model=list[schemas.IterationResponse])
async def list_iterations(agent_id: str, db: DbSession):
    """List optimization iterations for an Agent."""
    agent = await _get_agent(db, agent_id)

    result = await db.execute(
        select(OptimizationIteration)
        .where(
            OptimizationIteration.tenant_id == agent.tenant_id,
            OptimizationIteration.agent_id == agent_id,
        )
        .order_by(OptimizationIteration.iteration_no.desc())
        .limit(20)
    )
    iterations = result.scalars().all()

    return [
        schemas.IterationResponse(
            id=it.id,
            tenant_id=it.tenant_id,
            agent_id=it.agent_id,
            iteration_no=it.iteration_no,
            status=it.status,
            baseline_release_id=it.baseline_release_id,
            created_at=it.created_at,
        )
        for it in iterations
    ]


@router.get("/iterations/{iteration_id}", response_model=schemas.IterationResponse)
async def get_iteration(agent_id: str, iteration_id: str, db: DbSession):
    """Get a single iteration by ID."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)
    return schemas.IterationResponse(
        id=iteration.id,
        tenant_id=iteration.tenant_id,
        agent_id=iteration.agent_id,
        iteration_no=iteration.iteration_no,
        status=iteration.status,
        baseline_release_id=iteration.baseline_release_id,
        created_at=iteration.created_at,
    )


@router.post("/iterations/{iteration_id}/cancel")
async def cancel_iteration(agent_id: str, iteration_id: str, db: DbSession):
    """Cancel a running iteration."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)
    iteration.status = "cancelled"
    iteration.completed_at = utcnow()
    await db.flush()
    return {"status": "cancelled"}


# ── Approval and Publication ─────────────────────────────────────────────

@router.post("/iterations/{iteration_id}/approve")
async def approve_iteration(
    agent_id: str,
    iteration_id: str,
    body: schemas.ApproveRequest,
    db: DbSession,
):
    """Approve an iteration for publication."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    if iteration.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Iteration is not awaiting approval")

    iteration.status = "approved"
    await db.flush()
    return {"status": "approved", "iteration_id": iteration_id}


@router.post("/iterations/{iteration_id}/reject")
async def reject_iteration(
    agent_id: str,
    iteration_id: str,
    body: schemas.RejectRequest,
    db: DbSession,
):
    """Reject an iteration."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    if iteration.status not in ("awaiting_approval", "approved"):
        raise HTTPException(status_code=409, detail="Iteration cannot be rejected in current status")

    iteration.status = "rejected"
    iteration.error_json = json.dumps({"reason": body.reason}, ensure_ascii=False)
    iteration.completed_at = utcnow()
    await db.flush()
    return {"status": "rejected"}


# ── Candidates ───────────────────────────────────────────────────────────

@router.get("/iterations/{iteration_id}/candidates", response_model=list[schemas.CandidateResponse])
async def list_candidates(agent_id: str, iteration_id: str, db: DbSession):
    """List candidates for an iteration."""
    agent = await _get_agent(db, agent_id)
    # Verify iteration belongs to agent
    await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    result = await db.execute(
        select(OptimizationCandidate)
        .where(
            OptimizationCandidate.tenant_id == agent.tenant_id,
            OptimizationCandidate.iteration_id == iteration_id,
        )
        .order_by(OptimizationCandidate.attempt_number)
    )
    candidates = result.scalars().all()

    return [
        schemas.CandidateResponse(
            id=c.id,
            change_type=c.change_type,
            status=c.status,
            attempt_number=c.attempt_number,
            hypothesis=c.hypothesis,
            patch_hash=c.patch_hash,
        )
        for c in candidates
    ]


@router.post("/candidates/{candidate_id}/publish")
async def publish_candidate(
    agent_id: str,
    candidate_id: str,
    body: schemas.PublishRequest,
    db: DbSession,
):
    """Publish an approved candidate, creating a new release and switching the binding."""
    agent = await _get_agent(db, agent_id)

    candidate = await db.scalar(
        select(OptimizationCandidate).where(
            OptimizationCandidate.id == candidate_id,
            OptimizationCandidate.tenant_id == agent.tenant_id,
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if candidate.status != "approved":
        raise HTTPException(status_code=409, detail="Candidate must be approved before publishing")

    # Publish: create a new release and switch the binding
    from sales_agent.models.runtime_release import OptimizationRelease

    binding = await db.scalar(
        select(AgentRuntimeBinding).where(
            AgentRuntimeBinding.tenant_id == agent.tenant_id,
            AgentRuntimeBinding.agent_id == agent_id,
        )
    )
    if binding is None:
        raise HTTPException(status_code=409, detail="No runtime binding exists for this Agent")

    # Create new release from candidate
    release_service = ReleaseService(db)
    try:
        result = await release_service.activate(
            tenant_id=agent.tenant_id,
            agent_id=agent_id,
            release_id=candidate_id,  # candidate_id as release reference
            expected_lock_version=binding.lock_version,
            actor_id=body.actor_id,
        )
    except StaleRuntimeBinding:
        raise HTTPException(status_code=409, detail="Concurrent modification detected, retry")
    except ReleaseNotFound:
        raise HTTPException(status_code=404, detail="Release not found")

    candidate.status = "published"
    await db.flush()

    return {"status": "published", "release_id": result.release_id}


# ── Rollback ─────────────────────────────────────────────────────────────

@router.post("/releases/rollback")
async def rollback_release(
    agent_id: str,
    body: schemas.RollbackRequest,
    db: DbSession,
):
    """Roll back to a previous release."""
    agent = await _get_agent(db, agent_id)

    release_service = ReleaseService(db)
    try:
        target = await release_service.get_manifest(agent.tenant_id, body.target_release_id)
    except ReleaseNotFound:
        raise HTTPException(status_code=404, detail="Target release not found")

    binding = await db.scalar(
        select(AgentRuntimeBinding).where(
            AgentRuntimeBinding.tenant_id == agent.tenant_id,
            AgentRuntimeBinding.agent_id == agent_id,
        )
    )
    if binding is None:
        raise HTTPException(status_code=409, detail="No runtime binding exists")

    # Create a rollback release pointing to the target manifest
    from sales_agent.models.runtime_release import OptimizationRelease

    rollback_release = OptimizationRelease(
        id=generate_id(),
        tenant_id=agent.tenant_id,
        agent_id=agent_id,
        release_number=(await _next_release_number(db, agent.tenant_id, agent_id)),
        status="active",
        manifest_hash=target.manifest_hash,
        knowledge_version_id=target.knowledge_version_id,
        retrieval_profile_id=target.retrieval_profile_id,
        router_profile_id=target.router_profile_id,
        rollback_of_release_id=body.target_release_id,
        published_by=body.actor_id,
        published_at=utcnow(),
    )
    db.add(rollback_release)
    await db.flush()

    try:
        result = await release_service.activate(
            tenant_id=agent.tenant_id,
            agent_id=agent_id,
            release_id=rollback_release.id,
            expected_lock_version=binding.lock_version,
            actor_id=body.actor_id,
        )
    except StaleRuntimeBinding:
        raise HTTPException(status_code=409, detail="Concurrent modification detected, retry")

    return {"status": "rolled_back", "release_id": result.release_id, "rolled_back_to": body.target_release_id}


# ── Checkpoints ──────────────────────────────────────────────────────────

@router.get("/checkpoints")
async def list_checkpoints(
    agent_id: str,
    db: DbSession,
    iteration_id: str = Query(...),
):
    """List LangGraph checkpoints for an iteration."""
    agent = await _get_agent(db, agent_id)

    result = await db.execute(
        select(IterationGraphCheckpoint)
        .where(
            IterationGraphCheckpoint.tenant_id == agent.tenant_id,
            IterationGraphCheckpoint.iteration_id == iteration_id,
        )
        .order_by(IterationGraphCheckpoint.created_at.desc())
    )
    checkpoints = result.scalars().all()

    return [
        {
            "id": cp.id,
            "stage": cp.stage,
            "thread_id": cp.thread_id,
            "checkpoint_id": cp.checkpoint_id,
            "manifest_hash": cp.manifest_hash,
        }
        for cp in checkpoints
    ]


@router.post("/checkpoints/{checkpoint_id}/fork")
async def fork_checkpoint(
    agent_id: str,
    checkpoint_id: str,
    body: schemas.CheckpointForkRequest,
    db: DbSession,
):
    """Fork a checkpoint with a candidate manifest for counterfactual replay."""
    agent = await _get_agent(db, agent_id)

    cp = await db.scalar(
        select(IterationGraphCheckpoint).where(
            IterationGraphCheckpoint.id == checkpoint_id,
            IterationGraphCheckpoint.tenant_id == agent.tenant_id,
        )
    )
    if cp is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    # Create forked checkpoint referencing the candidate
    forked = IterationGraphCheckpoint(
        id=generate_id(),
        tenant_id=agent.tenant_id,
        iteration_id=cp.iteration_id,
        candidate_id=body.candidate_id,
        stage=cp.stage,
        thread_id=f"{cp.thread_id}:fork:{body.candidate_id}",
        parent_checkpoint_id=cp.checkpoint_id,
    )
    db.add(forked)
    await db.flush()

    return {"status": "forked", "forked_checkpoint_id": forked.id, "thread_id": forked.thread_id}


# ── Events ───────────────────────────────────────────────────────────────────


def _event_to_response(event: Any) -> schemas.EventResponse:
    """Convert an IterationEvent ORM object to an API response."""
    import json as _json
    payload = {}
    try:
        payload = _json.loads(event.payload_json) if event.payload_json else {}
    except (TypeError, ValueError):
        pass
    return schemas.EventResponse(
        id=event.id,
        sequence_no=event.sequence_no,
        event_type=event.event_type,
        stage=event.stage,
        status=event.status,
        progress_current=event.progress_current,
        progress_total=event.progress_total,
        message=event.message,
        payload=payload,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        created_at=event.created_at,
    )


@router.get(
    "/iterations/{iteration_id}/events",
    response_model=schemas.EventPageResponse,
)
async def list_events(
    agent_id: str,
    iteration_id: str,
    db: DbSession,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Replay events for an iteration after a given sequence cursor."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.optimization.event_service import IterationEventService

    service = IterationEventService(db)
    events = await service.list_after(
        tenant_id=agent.tenant_id,
        iteration_id=iteration.id,
        after_sequence=after_sequence,
        limit=limit,
    )
    next_seq = events[-1].sequence_no if events else after_sequence
    terminal = iteration.status in ("completed", "cancelled", "failed", "rolled_back")

    return schemas.EventPageResponse(
        events=[_event_to_response(e) for e in events],
        next_sequence=next_seq,
        terminal=terminal,
    )


@router.get(
    "/iterations/{iteration_id}/events/wait",
    response_model=schemas.EventPageResponse,
)
async def wait_events(
    agent_id: str,
    iteration_id: str,
    db: DbSession,
    after_sequence: int = Query(default=0, ge=0),
    timeout_seconds: int = Query(default=30, ge=1, le=30),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Long-poll for new events. Returns immediately if events are available,
    otherwise waits up to *timeout_seconds*."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.optimization.event_service import IterationEventService

    service = IterationEventService(db)
    result = await service.wait_after(
        tenant_id=agent.tenant_id,
        iteration_id=iteration.id,
        after_sequence=after_sequence,
        timeout_seconds=timeout_seconds,
        limit=limit,
    )
    return schemas.EventPageResponse(
        events=[_event_to_response(e) for e in result.events],
        next_sequence=result.next_sequence,
        terminal=result.terminal,
    )


@router.get("/iterations/{iteration_id}/events/stream")
async def stream_events(
    agent_id: str,
    iteration_id: str,
    db: DbSession,
    last_event_id: str = Query(default="", alias="Last-Event-ID"),
):
    """SSE endpoint for live event streaming.

    Supports reconnect with ``Last-Event-ID`` header or query parameter.
    Emits a heartbeat comment every 15 seconds and closes on terminal state.
    """
    import asyncio
    import json as _json

    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.optimization.event_service import IterationEventService, TERMINAL_STATES

    async def event_generator():
        cursor = 0
        try:
            cursor = int(last_event_id) if last_event_id else 0
        except (ValueError, TypeError):
            cursor = 0

        heartbeat_interval = 15
        last_heartbeat = time.monotonic()

        while True:
            service = IterationEventService(db)
            events = await service.list_after(
                tenant_id=agent.tenant_id,
                iteration_id=iteration.id,
                after_sequence=cursor,
                limit=50,
            )
            for event in events:
                resp = _event_to_response(event)
                yield f"id: {event.sequence_no}\n"
                yield f"data: {_json.dumps(resp.model_dump(), ensure_ascii=False)}\n\n"
                cursor = event.sequence_no
                last_heartbeat = time.monotonic()

            # Check terminal
            await db.refresh(iteration)
            if iteration.status in TERMINAL_STATES and not events:
                yield f"id: {cursor}\n"
                yield f"data: {_json.dumps({'terminal': True})}\n\n"
                return

            # Heartbeat every 15 seconds
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Reports ──────────────────────────────────────────────────────────────────


def _report_to_summary(r: Any) -> schemas.ReportSummaryResponse:
    hard_gates = {}
    try:
        hard_gates = json.loads(r.hard_gates_json) if r.hard_gates_json else {}
    except (TypeError, ValueError):
        pass
    return schemas.ReportSummaryResponse(
        id=r.id,
        tenant_id=r.tenant_id,
        agent_id=r.agent_id,
        iteration_id=r.iteration_id,
        report_type=r.report_type,
        candidate_id=r.candidate_id,
        candidate_key=r.candidate_key,
        release_id=r.release_id,
        report_version=r.report_version,
        formula_version=r.formula_version,
        status=r.status,
        recommendation=r.recommendation,
        effect_index_before=r.effect_index_before,
        effect_index_after=r.effect_index_after,
        effect_index_delta=r.effect_index_delta,
        hard_gates=hard_gates,
        data_snapshot_hash=r.data_snapshot_hash,
        created_at=r.created_at,
    )


@router.get(
    "/iterations/{iteration_id}/reports",
    response_model=list[schemas.ReportSummaryResponse],
)
async def list_reports(agent_id: str, iteration_id: str, db: DbSession):
    """List all reports for an iteration."""
    agent = await _get_agent(db, agent_id)
    iteration = await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.models.iteration_observability import IterationReport

    result = await db.execute(
        select(IterationReport)
        .where(
            IterationReport.tenant_id == agent.tenant_id,
            IterationReport.iteration_id == iteration_id,
        )
        .order_by(IterationReport.created_at.desc())
    )
    reports = result.scalars().all()
    return [_report_to_summary(r) for r in reports]


@router.get(
    "/iterations/{iteration_id}/reports/{report_id}",
    response_model=schemas.ReportDetailResponse,
)
async def get_report(agent_id: str, iteration_id: str, report_id: str, db: DbSession):
    """Get a full report with metric groups and case classifications."""
    agent = await _get_agent(db, agent_id)
    await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.models.iteration_observability import (
        IterationReport, IterationReportMetric, IterationReportCase,
    )

    report = await db.scalar(
        select(IterationReport).where(
            IterationReport.id == report_id,
            IterationReport.tenant_id == agent.tenant_id,
        )
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    metrics_result = await db.execute(
        select(IterationReportMetric).where(
            IterationReportMetric.report_id == report_id,
        )
    )
    metric_rows = metrics_result.scalars().all()

    cases_result = await db.execute(
        select(IterationReportCase).where(
            IterationReportCase.report_id == report_id,
        )
    )
    case_rows = cases_result.scalars().all()

    groups_map: dict[str, dict[str, Any]] = {}
    for m in metric_rows:
        g = groups_map.setdefault(m.group_name, {
            "group_name": m.group_name,
            "score_before": None,
            "score_after": None,
            "delta": None,
            "coverage": 0,
            "total_metrics": 0,
            "metrics": [],
        })
        g["total_metrics"] += 1
        if m.applicable:
            g["coverage"] += 1
        g["metrics"].append({
            "metric_name": m.metric_name,
            "direction": m.direction,
            "weight": m.weight,
            "before_value": m.before_value,
            "after_value": m.after_value,
            "before_normalized": m.before_normalized,
            "after_normalized": m.after_normalized,
            "delta": m.delta,
            "applicable": m.applicable,
            "gate_result": m.gate_result,
        })

    summary = _report_to_summary(report)
    return schemas.ReportDetailResponse(
        **summary.model_dump(),
        groups=list(groups_map.values()),
        cases=[
            schemas.ReportCaseResponse(
                case_id=c.case_id,
                classification=c.classification,
                cause=c.cause,
                before_pass=c.before_pass,
                after_pass=c.after_pass,
                score_delta=c.score_delta,
                rank_delta=c.rank_delta,
                latency_delta_ms=c.latency_delta_ms,
                token_delta=c.token_delta,
            )
            for c in case_rows
        ],
    )


@router.get("/iterations/{iteration_id}/reports/{report_id}/artifacts/{format}")
async def get_report_artifact(
    agent_id: str, iteration_id: str, report_id: str, format: str, db: DbSession,
):
    """Download a report artifact in the requested format (json/markdown/html/csv)."""
    from fastapi.responses import PlainTextResponse

    if format not in ("json", "markdown", "html", "csv"):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")

    agent = await _get_agent(db, agent_id)
    await _get_iteration(db, agent.tenant_id, agent_id, iteration_id)

    from sales_agent.models.iteration_observability import (
        IterationReport, IterationReportMetric, IterationReportCase,
    )
    from sales_agent.optimization.reporting.renderers import RENDERERS

    report = await db.scalar(
        select(IterationReport).where(
            IterationReport.id == report_id,
            IterationReport.tenant_id == agent.tenant_id,
        )
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    metrics_result = await db.execute(
        select(IterationReportMetric).where(
            IterationReportMetric.report_id == report_id,
        )
    )
    metric_rows = metrics_result.scalars().all()

    cases_result = await db.execute(
        select(IterationReportCase).where(
            IterationReportCase.report_id == report_id,
        )
    )
    case_rows = cases_result.scalars().all()

    hard_gates = {}
    try:
        hard_gates = json.loads(report.hard_gates_json) if report.hard_gates_json else {}
    except (TypeError, ValueError):
        pass

    doc: dict[str, Any] = {
        "report_id": report.id,
        "report_type": report.report_type,
        "recommendation": report.recommendation,
        "effect_index_before": report.effect_index_before,
        "effect_index_after": report.effect_index_after,
        "effect_index_delta": report.effect_index_delta,
        "hard_gates": hard_gates,
        "formula_version": report.formula_version,
        "data_snapshot_hash": report.data_snapshot_hash,
        "created_at": report.created_at,
        "groups": [
            {
                "group_name": m.group_name,
                "weight": 0.0,
                "score_before": None,
                "score_after": None,
                "delta": None,
                "coverage": 0,
                "total_metrics": 0,
                "metrics": [],
            }
            for m in metric_rows
        ],
        "cases": [
            {
                "case_id": c.case_id,
                "classification": c.classification,
                "cause": c.cause,
                "before_pass": c.before_pass,
                "after_pass": c.after_pass,
                "score_delta": c.score_delta,
                "rank_delta": c.rank_delta,
                "latency_delta_ms": c.latency_delta_ms,
                "token_delta": c.token_delta,
            }
            for c in case_rows
        ],
    }

    content = RENDERERS[format](doc)
    media_types = {
        "json": "application/json",
        "markdown": "text/markdown; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
    }
    return PlainTextResponse(
        content=content,
        media_type=media_types.get(format, "text/plain"),
        headers={"Content-Disposition": f"inline; filename=report.{format}"},
    )


# ── Trends ───────────────────────────────────────────────────────────────────


@router.get("/optimization/trends", response_model=schemas.TrendResponse)
async def get_trends(
    agent_id: str,
    db: DbSession,
    limit: int = Query(default=10, ge=1, le=10),
):
    """Return the latest completed final reports for trend analysis."""
    agent = await _get_agent(db, agent_id)

    from sales_agent.models.iteration_observability import IterationReport

    result = await db.execute(
        select(IterationReport)
        .where(
            IterationReport.tenant_id == agent.tenant_id,
            IterationReport.agent_id == agent_id,
            IterationReport.report_type == "final",
            IterationReport.status == "ready",
        )
        .order_by(IterationReport.created_at.desc())
        .limit(limit)
    )
    reports = result.scalars().all()

    trends: list[dict[str, Any]] = []
    for r in reports:
        hard_gates = {}
        try:
            hard_gates = json.loads(r.hard_gates_json) if r.hard_gates_json else {}
        except (TypeError, ValueError):
            pass
        trends.append({
            "report_id": r.id,
            "iteration_id": r.iteration_id,
            "recommendation": r.recommendation,
            "effect_index_before": r.effect_index_before,
            "effect_index_after": r.effect_index_after,
            "effect_index_delta": r.effect_index_delta,
            "hard_gates": hard_gates,
            "created_at": r.created_at,
        })

    return schemas.TrendResponse(agent_id=agent_id, trends=trends)


# ── Helpers ──────────────────────────────────────────────────────────────

async def _next_release_number(db: AsyncSession, tenant_id: str, agent_id: str) -> int:
    from sales_agent.models.runtime_release import OptimizationRelease
    max_rel = await db.scalar(
        select(func.max(OptimizationRelease.release_number)).where(
            OptimizationRelease.tenant_id == tenant_id,
            OptimizationRelease.agent_id == agent_id,
        )
    )
    return (max_rel or 0) + 1
