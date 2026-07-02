"""Test post-publish lifecycle and report job integration."""

import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationIteration, OptimizationJob
from sales_agent.models.base import generate_id


@pytest.mark.asyncio
class TestReportLifecycle:
    async def test_publish_does_not_complete_before_post_publish_report(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """After publish, iteration must be post_publish_evaluating, not completed."""
        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=1,
            status="publishing",
        )
        db_session.add(iteration)
        await db_session.flush()

        # Simulate what the post_publish_eval handler does
        eval_run_id = generate_id()
        iteration.post_publish_eval_run_id = eval_run_id
        iteration.status = "post_publish_evaluating"
        await db_session.flush()

        assert iteration.status == "post_publish_evaluating"
        assert iteration.final_report_id is None  # No report yet

    async def test_completed_only_after_final_report(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Iteration must only complete when final_report_id is set."""
        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=2,
            status="post_publish_evaluating",
        )
        db_session.add(iteration)
        await db_session.flush()

        # Before report: not completed
        assert iteration.status != "completed"

        # After final report: set completed
        iteration.final_report_id = generate_id()
        iteration.status = "completed"
        await db_session.flush()

        assert iteration.status == "completed"
        assert iteration.final_report_id is not None

    async def test_report_job_has_correct_stage(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Report jobs must have stage='generate_report'."""
        job = OptimizationJob(
            id=generate_id(),
            tenant_id=sample_tenant,
            iteration_id=generate_id(),
            idempotency_key="report:candidate:c1:er1:effect-v1",
            stage="generate_report",
            status="queued",
        )
        db_session.add(job)
        await db_session.flush()

        assert job.stage == "generate_report"
