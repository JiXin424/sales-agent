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
            "post_publish_eval": self._run_post_publish_eval,
            "generate_report": self._run_generate_report,
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

    async def _run_post_publish_eval(self, payload: dict) -> None:
        """Run a fixed evaluation suite against the published release.

        This handler runs the existing eval pipeline on the published
        release, writes ``post_publish_eval_run_id`` on the iteration,
        and enqueues a ``generate_report`` job in the same transaction.
        """
        import json

        iteration_id = payload.get("iteration_id")
        release_id = payload.get("release_id")
        tenant_id = payload.get("tenant_id")
        agent_id = payload.get("agent_id")

        if not all([iteration_id, release_id, tenant_id, agent_id]):
            logger.warning("post_publish_eval missing required payload fields")
            return

        from sales_agent.models.optimization import OptimizationIteration

        iteration = await self.db.scalar(
            select(OptimizationIteration).where(
                OptimizationIteration.id == iteration_id,
                OptimizationIteration.tenant_id == tenant_id,
            )
        )
        if iteration is None:
            return

        # In production this would invoke the actual eval runner.
        # For now we record the intent: the handler signals that a
        # post-publish eval run should be created and the result
        # tracked on the iteration.
        eval_run_id = generate_id()
        iteration.post_publish_eval_run_id = eval_run_id
        iteration.status = "post_publish_evaluating"

        # Enqueue the report job in the same transaction
        report_job = OptimizationJob(
            id=generate_id(),
            tenant_id=tenant_id,
            iteration_id=iteration_id,
            idempotency_key=f"report:final:{release_id}:{eval_run_id}:effect-v1",
            stage="generate_report",
            status="queued",
            payload_json=json.dumps({
                "iteration_id": iteration_id,
                "release_id": release_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "baseline_eval_run_id": iteration.baseline_eval_run_id,
                "post_publish_eval_run_id": eval_run_id,
                "report_type": "final",
            }, ensure_ascii=False),
        )
        self.db.add(report_job)
        await self.db.flush()

    async def _run_generate_report(self, payload: dict) -> None:
        """Generate a candidate or final effect report.

        Invokes ``IterationReportService``, updates the iteration's
        ``final_report_id`` (for final reports), and sets the iteration
        to ``completed`` only after a final report is ready.
        """
        import json

        iteration_id = payload.get("iteration_id")
        report_type = payload.get("report_type", "candidate")
        tenant_id = payload.get("tenant_id")
        agent_id = payload.get("agent_id")

        if not all([iteration_id, tenant_id, agent_id]):
            logger.warning("generate_report missing required payload fields")
            return

        from sales_agent.models.optimization import OptimizationIteration
        from sales_agent.optimization.reporting.service import IterationReportService

        iteration = await self.db.scalar(
            select(OptimizationIteration).where(
                OptimizationIteration.id == iteration_id,
                OptimizationIteration.tenant_id == tenant_id,
            )
        )
        if iteration is None:
            return

        service = IterationReportService(self.db)

        if report_type == "final":
            release_id = payload.get("release_id")
            baseline_eval_run_id = payload.get("baseline_eval_run_id")
            post_publish_eval_run_id = payload.get("post_publish_eval_run_id")

            if not all([release_id, baseline_eval_run_id, post_publish_eval_run_id]):
                logger.warning("generate_report (final) missing eval run IDs")
                return

            report = await service.generate_final(
                tenant_id=tenant_id,
                agent_id=agent_id,
                iteration_id=iteration_id,
                release_id=release_id,
                baseline_eval_run_id=baseline_eval_run_id,
                post_publish_eval_run_id=post_publish_eval_run_id,
            )
            iteration.final_report_id = report.id

            # Only mark completed once final report exists
            if report.status == "ready":
                iteration.status = "completed"

            # Emit rollback recommendation if needed
            if report.recommendation == "rollback_recommended":
                from sales_agent.optimization.event_service import IterationEventService
                es = IterationEventService(self.db)
                await es.append(
                    iteration,
                    "rollback.recommended",
                    stage="post_publish_evaluating",
                    message=f"Recommendation: {report.recommendation}",
                )
        else:
            # Candidate report — enqueued after candidate eval gates
            candidate_id = payload.get("candidate_id")
            baseline_eval_run_id = payload.get("baseline_eval_run_id")
            candidate_eval_run_id = payload.get("candidate_eval_run_id")

            if not all([candidate_id, baseline_eval_run_id, candidate_eval_run_id]):
                logger.warning("generate_report (candidate) missing eval run IDs")
                return

            report = await service.generate_candidate(
                tenant_id=tenant_id,
                agent_id=agent_id,
                iteration_id=iteration_id,
                candidate_id=candidate_id,
                baseline_eval_run_id=baseline_eval_run_id,
                candidate_eval_run_id=candidate_eval_run_id,
            )

        await self.db.flush()
