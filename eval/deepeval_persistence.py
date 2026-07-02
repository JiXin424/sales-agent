"""Persist DeepEval results to PostgreSQL via the new eval trace models.

Replaces the previous file-only JSON/HTML output with DB-backed persistence
while keeping file exports as projections of persisted runs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.eval import EvalRun, EvalRunResult
from sales_agent.models.eval_trace import EvalMetricResult
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


class EvalPersistence:
    """Save and query evaluation results against the new trace schema."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def save_metric_result(
        self,
        *,
        tenant_id: str,
        eval_run_result_id: str,
        eval_run_id: str,
        metric_name: str,
        score: float | None,
        threshold: float | None,
        passed: bool | None,
        applicability: str = "applicable",
        reason: str | None = None,
        judge_model: str | None = None,
        eval_cost: float | None = None,
    ) -> EvalMetricResult:
        """Persist one metric result row."""
        row = EvalMetricResult(
            id=generate_id(),
            tenant_id=tenant_id,
            eval_run_result_id=eval_run_result_id,
            eval_run_id=eval_run_id,
            metric_name=metric_name,
            score=score,
            threshold=threshold,
            passed=passed,
            applicability=applicability,
            reason=reason,
            judge_model=judge_model,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def save_case_result(
        self,
        *,
        tenant_id: str,
        eval_run_id: str,
        eval_case_id: str,
        passed: bool,
        metric_results: list[dict[str, Any]],
        actual_output: dict[str, Any] | None = None,
        route_type: str | None = None,
        route_confidence: float | None = None,
        retrieval_triggered: bool | None = None,
        source_count: int | None = None,
        fact_coverage: float | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> EvalRunResult:
        """Persist one case result with all its metric rows."""
        result = EvalRunResult(
            id=generate_id(),
            tenant_id=tenant_id,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            passed="true" if passed else "false",
            actual_output_json=json.dumps(actual_output or {}, ensure_ascii=False),
            route_type=route_type,
            route_confidence=route_confidence,
            retrieval_triggered=retrieval_triggered,
            source_count=source_count,
            fact_coverage=fact_coverage,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        self.db.add(result)
        await self.db.flush()

        for mr in metric_results:
            await self.save_metric_result(
                tenant_id=tenant_id,
                eval_run_result_id=result.id,
                eval_run_id=eval_run_id,
                metric_name=mr.get("metric_name", "unknown"),
                score=mr.get("score"),
                threshold=mr.get("threshold"),
                passed=mr.get("passed"),
                applicability=mr.get("applicability", "applicable"),
                reason=mr.get("reason"),
                judge_model=mr.get("judge_model"),
            )

        return result

    async def get_metric_results_for_run(
        self, tenant_id: str, eval_run_id: str,
    ) -> list[EvalMetricResult]:
        """Return all metric results for an eval run."""
        result = await self.db.execute(
            select(EvalMetricResult).where(
                EvalMetricResult.tenant_id == tenant_id,
                EvalMetricResult.eval_run_id == eval_run_id,
            )
        )
        return list(result.scalars().all())
