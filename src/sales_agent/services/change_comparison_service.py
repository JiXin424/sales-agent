"""Prompt/RAG 变更对比服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.eval import EvalCase, EvalRun, EvalRunResult
from sales_agent.models.review_item import ReviewItem

logger = logging.getLogger(__name__)


class ChangeComparisonService:
    """变更前后质量对比。

    用法::

        svc = ChangeComparisonService(db)
        result = await svc.compare_eval_runs(tenant_id, before_run_id, after_run_id)
        result = await svc.compare_review_outcomes(tenant_id, before_id, after_id, start, end)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compare_eval_runs(
        self,
        tenant_id: str,
        before_run_id: str,
        after_run_id: str,
    ) -> dict[str, Any]:
        """对比两次评估运行结果。

        返回通过率变化、新增失败、新增通过、回归数、改善数。
        """
        # 获取两次运行的结果
        before_results = await self._get_results_map(tenant_id, before_run_id)
        after_results = await self._get_results_map(tenant_id, after_run_id)

        # 获取运行概况
        before_run = await self._get_run(tenant_id, before_run_id)
        after_run = await self._get_run(tenant_id, after_run_id)

        before_pass_rate = (
            before_run.passed / before_run.total_cases
            if before_run and before_run.total_cases
            else 0
        )
        after_pass_rate = (
            after_run.passed / after_run.total_cases
            if after_run and after_run.total_cases
            else 0
        )

        # 找出变化：按 eval_case_id 匹配
        new_failures = []
        new_passes = []
        for case_id, after_r in after_results.items():
            before_r = before_results.get(case_id)
            if before_r:
                if before_r.passed == "true" and after_r.passed == "false":
                    new_failures.append({
                        "case_id": case_id,
                        "failure_reasons": json.loads(after_r.failure_reasons_json),
                    })
                elif before_r.passed == "false" and after_r.passed == "true":
                    new_passes.append({"case_id": case_id})

        return {
            "before_run_id": before_run_id,
            "after_run_id": after_run_id,
            "before_pass_rate": round(before_pass_rate, 3),
            "after_pass_rate": round(after_pass_rate, 3),
            "changed_pass_rate": round(after_pass_rate - before_pass_rate, 3),
            "regression_count": len(new_failures),
            "improvement_count": len(new_passes),
            "new_failures": new_failures,
            "new_passes": new_passes,
        }

    async def compare_review_outcomes(
        self,
        tenant_id: str,
        before_date: str,
        after_date: str,
    ) -> dict[str, Any]:
        """对比变更前后的审查条目分布。

        按原因分组统计变更前和变更后的数量。
        """
        # 变更前
        before_stmt = (
            select(ReviewItem.reason, func.count())
            .where(
                ReviewItem.tenant_id == tenant_id,
                ReviewItem.created_at < before_date,
            )
            .group_by(ReviewItem.reason)
        )
        before_result = dict((await self.db.execute(before_stmt)).all())

        # 变更后
        after_stmt = (
            select(ReviewItem.reason, func.count())
            .where(
                ReviewItem.tenant_id == tenant_id,
                ReviewItem.created_at >= after_date,
            )
            .group_by(ReviewItem.reason)
        )
        after_result = dict((await self.db.execute(after_stmt)).all())

        all_reasons = set(before_result.keys()) | set(after_result.keys())
        comparison = {}
        for reason in all_reasons:
            before_count = before_result.get(reason, 0)
            after_count = after_result.get(reason, 0)
            comparison[reason] = {
                "before": before_count,
                "after": after_count,
                "change": after_count - before_count,
            }

        return {
            "before_date": before_date,
            "after_date": after_date,
            "reason_comparison": comparison,
            "total_before": sum(before_result.values()),
            "total_after": sum(after_result.values()),
        }

    async def compare_document_change(
        self,
        tenant_id: str,
        before_date: str,
        after_date: str,
    ) -> dict[str, Any]:
        """对比文档变更前后的质量指标变化。

        统计变更前后的错误率、负反馈数、检索缺失数。
        """
        from sales_agent.models.agent_run import AgentRun
        from sales_agent.models.conversation import Conversation
        from sales_agent.models.feedback import Feedback

        # 错误率
        before_errors = (await self.db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.status == "failed",
                AgentRun.created_at < before_date,
            )
        )).scalar() or 0

        after_errors = (await self.db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.status == "failed",
                AgentRun.created_at >= after_date,
            )
        )).scalar() or 0

        # 负反馈
        before_down = (await self.db.execute(
            select(func.count()).select_from(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.rating == "down",
                Feedback.created_at < before_date,
            )
        )).scalar() or 0

        after_down = (await self.db.execute(
            select(func.count()).select_from(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.rating == "down",
                Feedback.created_at >= after_date,
            )
        )).scalar() or 0

        return {
            "before_date": before_date,
            "after_date": after_date,
            "error_count": {"before": before_errors, "after": after_errors, "change": after_errors - before_errors},
            "negative_feedback": {"before": before_down, "after": after_down, "change": after_down - before_down},
        }

    async def _get_results_map(
        self, tenant_id: str, run_id: str
    ) -> dict[str, EvalRunResult]:
        """获取运行结果，按 eval_case_id 建立映射。"""
        stmt = select(EvalRunResult).where(
            EvalRunResult.eval_run_id == run_id,
            EvalRunResult.tenant_id == tenant_id,
        )
        results = list((await self.db.execute(stmt)).scalars().all())
        return {r.eval_case_id: r for r in results}

    async def _get_run(self, tenant_id: str, run_id: str) -> EvalRun | None:
        """获取评估运行记录。"""
        stmt = select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
