"""Test report service: idempotency, snapshot hash, report generation."""

import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationIteration
from sales_agent.models.eval import EvalRunResult, EvalRun
from sales_agent.models.eval_trace import EvalMetricResult
from sales_agent.models.iteration_observability import (
    IterationReport,
    IterationReportMetric,
    IterationReportCase,
)
from sales_agent.models.base import generate_id


@pytest.mark.asyncio
class TestReportServiceAsync:
    """Tests that need the DB session."""

    async def test_candidate_report_persists_rows(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Generating a candidate report must persist report + metrics + cases."""
        from sales_agent.optimization.reporting.service import IterationReportService

        # Create iteration
        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=1,
            status="running",
        )
        db_session.add(iteration)
        await db_session.flush()

        # Create baseline eval run
        baseline_run = EvalRun(
            id=generate_id(),
            tenant_id=sample_tenant,
            eval_suite_id="suite1",
            status="completed",
        )
        db_session.add(baseline_run)

        # Create candidate eval run
        candidate_run = EvalRun(
            id=generate_id(),
            tenant_id=sample_tenant,
            eval_suite_id="suite1",
            status="completed",
        )
        db_session.add(candidate_run)
        await db_session.flush()

        # Add metric results
        for run_id in (baseline_run.id, candidate_run.id):
            for metric_name, score in [
                ("answer_correctness", 0.7 if run_id == baseline_run.id else 0.8),
                ("factual_accuracy", 0.7 if run_id == baseline_run.id else 0.8),
                ("completeness", 0.7 if run_id == baseline_run.id else 0.8),
            ]:
                db_session.add(EvalMetricResult(
                    id=generate_id(),
                    tenant_id=sample_tenant,
                    eval_run_id=run_id,
                    eval_run_result_id=generate_id(),
                    metric_name=metric_name,
                    score=score,
                    applicability="applicable",
                ))
        await db_session.flush()

        # Add case results
        for run_id, passed in [(baseline_run.id, True), (candidate_run.id, True)]:
            db_session.add(EvalRunResult(
                id=generate_id(),
                tenant_id=sample_tenant,
                eval_run_id=run_id,
                case_id="case1",
                passed=passed,
                score=0.7 if run_id == baseline_run.id else 0.8,
            ))
        await db_session.flush()

        service = IterationReportService(db_session)
        report = await service.generate_candidate(
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_id=iteration.id,
            candidate_id="c1",
            baseline_eval_run_id=baseline_run.id,
            candidate_eval_run_id=candidate_run.id,
        )

        assert report.report_type == "candidate"
        assert report.status == "ready"
        assert report.candidate_key == "c1"
        assert report.effect_index_before is not None
        assert report.effect_index_after is not None

        # Check child rows exist
        metrics = await db_session.execute(
            select(IterationReportMetric).where(
                IterationReportMetric.report_id == report.id,
            )
        )
        metric_rows = metrics.scalars().all()
        assert len(metric_rows) > 0

        cases = await db_session.execute(
            select(IterationReportCase).where(
                IterationReportCase.report_id == report.id,
            )
        )
        case_rows = cases.scalars().all()
        assert len(case_rows) > 0

    async def test_report_idempotent_same_hash(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Generating the same report twice must return the same row."""
        from sales_agent.optimization.reporting.service import IterationReportService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=2,
            status="running",
        )
        db_session.add(iteration)

        run = EvalRun(
            id=generate_id(),
            tenant_id=sample_tenant,
            eval_suite_id="suite1",
            status="completed",
        )
        db_session.add(run)
        await db_session.flush()

        for metric_name, score in [("answer_correctness", 0.7)]:
            db_session.add(EvalMetricResult(
                id=generate_id(),
                tenant_id=sample_tenant,
                eval_run_id=run.id,
                eval_run_result_id=generate_id(),
                metric_name=metric_name,
                score=score,
                applicability="applicable",
            ))
        await db_session.flush()

        service = IterationReportService(db_session)
        r1 = await service.generate_candidate(
            tenant_id=sample_tenant, agent_id=active_agent.id,
            iteration_id=iteration.id, candidate_id="c1",
            baseline_eval_run_id=run.id, candidate_eval_run_id=run.id,
        )
        r2 = await service.generate_candidate(
            tenant_id=sample_tenant, agent_id=active_agent.id,
            iteration_id=iteration.id, candidate_id="c1",
            baseline_eval_run_id=run.id, candidate_eval_run_id=run.id,
        )

        assert r1.id == r2.id  # same report row returned
