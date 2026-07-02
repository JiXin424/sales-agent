"""Optimization API routes: Agent-scoped iteration lifecycle.

All routes enforce Agent tenant ownership. Thread IDs use the
``kbopt:{tenant}:{iteration}:{candidate}:{run}`` prefix.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
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
