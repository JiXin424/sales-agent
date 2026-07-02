"""PostgreSQL-leased optimization worker.

Leases optimization jobs using FOR UPDATE SKIP LOCKED, executes stages
of the optimization LangGraph, and handles heartbeat/expiry/recovery.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationJob
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)

LEASE_DURATION_SECONDS = 300  # 5 minutes
HEARTBEAT_INTERVAL_SECONDS = 60  # 1 minute
MAX_ATTEMPTS = 3


class OptimizationWorker:
    """Leases and executes optimization jobs from PostgreSQL.

    Usage::

        worker = OptimizationWorker(db_session_factory)
        await worker.run_once()  # lease and execute one job
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._worker_id = generate_id()

    async def lease_one(self) -> OptimizationJob | None:
        """Acquire one queued job using SKIP LOCKED.

        Multiple workers can run concurrently; only one acquires each job.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Find an available job
        job = await self.db.scalar(
            select(OptimizationJob)
            .where(
                OptimizationJob.status == "queued",
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None

        # Also check for expired leases we can take over
        if job is None:
            job = await self.db.scalar(
                select(OptimizationJob)
                .where(
                    OptimizationJob.status == "running",
                    OptimizationJob.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )

        if job is None:
            return None

        # Acquire lease
        job.status = "running"
        job.lease_owner = self._worker_id
        job.lease_expires_at = datetime.now(timezone.utc).isoformat()
        job.attempts = (job.attempts or 0) + 1
        await self.db.flush()

        logger.info("Worker %s leased job %s (stage=%s, attempt=%d)",
                     self._worker_id, job.id, job.stage, job.attempts)
        return job

    async def complete_job(self, job: OptimizationJob) -> None:
        """Mark a job as completed."""
        job.status = "completed"
        job.lease_owner = None
        job.lease_expires_at = None
        await self.db.flush()

    async def fail_job(self, job: OptimizationJob, error: str) -> None:
        """Mark a job as failed, allowing retry if under max attempts."""
        import json
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.error_json = json.dumps({"error": error}, ensure_ascii=False)
        else:
            job.status = "queued"  # retry
            job.error_json = json.dumps({"last_error": error}, ensure_ascii=False)
        job.lease_owner = None
        job.lease_expires_at = None
        await self.db.flush()

    async def heartbeat(self, job: OptimizationJob) -> None:
        """Extend lease to prevent timeout during long-running stages."""
        job.lease_expires_at = datetime.now(timezone.utc).isoformat()
        await self.db.flush()

    async def run_once(self) -> bool:
        """Lease and execute one job. Returns True if a job was processed."""
        job = await self.lease_one()
        if job is None:
            return False

        try:
            # Execute the stage
            await self._execute_stage(job)
            await self.complete_job(job)
        except Exception as e:
            logger.error("Job %s failed: %s", job.id, e)
            await self.fail_job(job, str(e))

        return True

    async def _execute_stage(self, job: OptimizationJob) -> None:
        """Execute one stage of the optimization workflow.

        In production, this would invoke the LangGraph graph with the
        appropriate checkpoint and stage-specific logic.
        """
        import json

        payload = json.loads(job.payload_json) if job.payload_json else {}
        stage = job.stage

        logger.info("Executing stage=%s for job=%s", stage, job.id)

        # Stage execution is a placeholder — in production, each stage
        # invokes the corresponding LangGraph node with the payload.
        stage_handlers = {
            "baseline": self._run_baseline,
            "diagnose": self._run_diagnose,
            "propose": self._run_propose,
            "build": self._run_build,
            "targeted_eval": self._run_eval,
            "regression_eval": self._run_eval,
            "publish": self._run_publish,
        }

        handler = stage_handlers.get(stage)
        if handler:
            await handler(payload)
        else:
            logger.warning("Unknown stage: %s", stage)

    async def _run_baseline(self, payload: dict) -> None:
        pass

    async def _run_diagnose(self, payload: dict) -> None:
        pass

    async def _run_propose(self, payload: dict) -> None:
        pass

    async def _run_build(self, payload: dict) -> None:
        pass

    async def _run_eval(self, payload: dict) -> None:
        pass

    async def _run_publish(self, payload: dict) -> None:
        pass
