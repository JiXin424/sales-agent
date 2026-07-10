"""Iteration report service: aggregate eval data, apply the effect formula,
and persist idempotent report rows.

Report generation never invokes the Agent or DeepEval — it only reads
already-persisted evaluation rows.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationIteration
from sales_agent.models.iteration_observability import (
    IterationReport,
    IterationReportMetric,
    IterationReportCase,
)
from sales_agent.models.eval_trace import EvalMetricResult
from sales_agent.models.eval import EvalRunResult
from sales_agent.models.base import generate_id

from .formula import (
    EFFECT_V1,
    classify_case,
    compute_effect,
    compute_snapshot_hash,
    get_formula,
)
from .types import ReportDecision, ReportType

logger = logging.getLogger(__name__)


def _to_bool(value: Any) -> bool | None:
    """Coerce EvalRunResult.passed (Text \"true\"/\"false\") → bool."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    if isinstance(value, int):
        return bool(value)
    return None

# Stable report version for this code path (bump when formula or classification
# logic changes in a way that should create a new report version).
REPORT_VERSION = 1


@dataclass
class GenerateReportRequest:
    """Inputs needed to generate a report."""

    tenant_id: str
    agent_id: str
    iteration_id: str
    report_type: ReportType
    candidate_id: str | None
    candidate_key: str  # candidate ID or "__final__"
    release_id: str | None
    baseline_eval_run_id: str
    candidate_eval_run_id: str
    formula_version: str = "effect-v1"


class IterationReportService:
    """Read eval data, compute the effect formula, and persist the report."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Public API ───────────────────────────────────────────────────────

    async def generate_candidate(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        iteration_id: str,
        candidate_id: str,
        baseline_eval_run_id: str,
        candidate_eval_run_id: str,
    ) -> IterationReport:
        """Generate (or return existing) candidate report."""
        return await self._generate(
            GenerateReportRequest(
                tenant_id=tenant_id,
                agent_id=agent_id,
                iteration_id=iteration_id,
                report_type="candidate",
                candidate_id=candidate_id,
                candidate_key=candidate_id,
                release_id=None,
                baseline_eval_run_id=baseline_eval_run_id,
                candidate_eval_run_id=candidate_eval_run_id,
            )
        )

    async def generate_final(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        iteration_id: str,
        release_id: str,
        baseline_eval_run_id: str,
        post_publish_eval_run_id: str,
    ) -> IterationReport:
        """Generate (or return existing) final report."""
        return await self._generate(
            GenerateReportRequest(
                tenant_id=tenant_id,
                agent_id=agent_id,
                iteration_id=iteration_id,
                report_type="final",
                candidate_id=None,
                candidate_key="__final__",
                release_id=release_id,
                baseline_eval_run_id=baseline_eval_run_id,
                candidate_eval_run_id=post_publish_eval_run_id,
            )
        )

    # ── Core ─────────────────────────────────────────────────────────────

    async def _generate(self, req: GenerateReportRequest) -> IterationReport:
        # 1. Idempotency check
        existing = await self._find_existing(req)
        if existing is not None and existing.status == "ready":
            return existing

        # 2. Load eval data
        baseline_metrics, candidate_metrics = await self._load_metric_values(
            req.tenant_id, req.baseline_eval_run_id, req.candidate_eval_run_id,
        )
        cases = await self._load_case_results(
            req.tenant_id, req.baseline_eval_run_id, req.candidate_eval_run_id,
        )
        hard_gates = await self._evaluate_hard_gates(req.tenant_id, cases)

        # 3. Compute snapshot hash
        snapshot_inputs: dict[str, Any] = {
            "formula_version": req.formula_version,
            "report_type": req.report_type,
            "baseline_eval_run_id": req.baseline_eval_run_id,
            "candidate_eval_run_id": req.candidate_eval_run_id,
            "baseline_metrics": dict(sorted(baseline_metrics.items())),
            "candidate_metrics": dict(sorted(candidate_metrics.items())),
            "hard_gates": dict(sorted(hard_gates.items())),
        }
        snapshot_hash = compute_snapshot_hash(snapshot_inputs)

        # 4. Re-check idempotency with hash
        if existing is not None and existing.data_snapshot_hash == snapshot_hash:
            existing.status = "ready"
            await self.db.flush()
            return existing

        # 5. Compute effect
        formula = get_formula(req.formula_version)
        decision = compute_effect(
            formula=formula,
            report_type=req.report_type,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            hard_gates=hard_gates,
        )

        # 6. Persist report row
        if existing is not None:
            report = existing
            report.report_version += 1
            # Remove old child rows
            for child_model in (IterationReportMetric, IterationReportCase):
                old = await self.db.execute(
                    select(child_model).where(child_model.report_id == report.id)
                )
                for row in old.scalars().all():
                    await self.db.delete(row)
        else:
            report = IterationReport(
                id=generate_id(),
                tenant_id=req.tenant_id,
                agent_id=req.agent_id,
                iteration_id=req.iteration_id,
                report_type=req.report_type,
                candidate_id=req.candidate_id,
                candidate_key=req.candidate_key,
                release_id=req.release_id,
                baseline_eval_run_id=req.baseline_eval_run_id,
                candidate_eval_run_id=req.candidate_eval_run_id,
                report_version=REPORT_VERSION,
                formula_version=req.formula_version,
            )

        report.status = "ready"
        report.recommendation = decision.recommendation
        report.effect_index_before = decision.composite_before
        report.effect_index_after = decision.composite_after
        report.effect_index_delta = decision.composite_delta
        report.hard_gates_json = json.dumps(
            {"failed": decision.hard_gates_failed}, ensure_ascii=False,
        )
        report.data_snapshot_hash = snapshot_hash
        report.summary_json = json.dumps(
            {
                "composite_before": decision.composite_before,
                "composite_after": decision.composite_after,
                "composite_delta": decision.composite_delta,
                "groups": len(decision.groups),
                "hard_gates_failed": decision.hard_gates_failed,
            },
            ensure_ascii=False,
        )

        if existing is None:
            self.db.add(report)
        await self.db.flush()

        # 7. Persist metric child rows
        for group in decision.groups:
            for me in group.metrics:
                metric_row = IterationReportMetric(
                    id=generate_id(),
                    tenant_id=req.tenant_id,
                    report_id=report.id,
                    group_name=group.group_name,
                    metric_name=me.metric_name,
                    direction=me.direction,
                    weight=me.weight,
                    before_value=me.before_value,
                    after_value=me.after_value,
                    before_normalized=me.before_normalized,
                    after_normalized=me.after_normalized,
                    delta=me.delta,
                    applicable=me.applicable,
                    gate_result=me.gate_result,
                )
                self.db.add(metric_row)

        # 8. Persist case child rows
        for case in cases:
            case_row = IterationReportCase(
                id=generate_id(),
                tenant_id=req.tenant_id,
                report_id=report.id,
                case_id=case.case_id,
                classification=case.classification,
                cause=case.cause,
                before_pass=case.before_pass,
                after_pass=case.after_pass,
                score_delta=case.score_delta,
                rank_delta=case.rank_delta,
                latency_delta_ms=case.latency_delta_ms,
                token_delta=case.token_delta,
                evidence_json=json.dumps(case.evidence, ensure_ascii=False),
            )
            self.db.add(case_row)

        await self.db.flush()
        return report

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _find_existing(self, req: GenerateReportRequest) -> IterationReport | None:
        return await self.db.scalar(
            select(IterationReport).where(
                IterationReport.tenant_id == req.tenant_id,
                IterationReport.iteration_id == req.iteration_id,
                IterationReport.report_type == req.report_type,
                IterationReport.candidate_key == req.candidate_key,
                IterationReport.report_version == REPORT_VERSION,
            )
        )

    async def _load_metric_values(
        self, tenant_id: str, baseline_run_id: str, candidate_run_id: str,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Load aggregated metric scores for both runs.

        For each metric name we take the mean score across applicable cases.
        """

        async def _agg(run_id: str) -> dict[str, float]:
            result = await self.db.execute(
                select(EvalMetricResult.metric_name, EvalMetricResult.score)
                .where(
                    EvalMetricResult.tenant_id == tenant_id,
                    EvalMetricResult.eval_run_id == run_id,
                    EvalMetricResult.applicability == "applicable",
                    EvalMetricResult.score.isnot(None),
                )
            )
            rows = result.all()
            # Group by metric_name and average
            from collections import defaultdict
            groups: dict[str, list[float]] = defaultdict(list)
            for metric_name, score in rows:
                # type: ignore[union-attr] — filtered by .isnot(None)
                groups[metric_name].append(float(score))  # type: ignore[arg-type]
            return {k: sum(v) / len(v) for k, v in groups.items()}

        return await _agg(baseline_run_id), await _agg(candidate_run_id)

    async def _load_case_results(
        self, tenant_id: str, baseline_run_id: str, candidate_run_id: str,
    ) -> Sequence[CaseEffect]:
        """Build per-case effect rows from eval run results."""
        # Load baseline results
        baseline_rows = await self._load_run_results(tenant_id, baseline_run_id)
        candidate_rows = await self._load_run_results(tenant_id, candidate_run_id)

        baseline_map: dict[str, EvalRunResult] = {}
        for row in baseline_rows:
            case_ref = getattr(row, "eval_case_id", None) or getattr(row, "case_id", None)
            if case_ref:
                baseline_map[case_ref] = row

        cases: list[CaseEffect] = []
        for crow in candidate_rows:
            case_id = getattr(crow, "eval_case_id", None) or getattr(crow, "case_id", None) or ""
            brow = baseline_map.pop(case_id, None)

            had_error = (crow.error_code and crow.error_code not in ("", None)) if hasattr(crow, "error_code") else False
            is_new = brow is None

            effect = classify_case(
                case_id=case_id,
                before_pass=_to_bool(brow.passed) if brow else None,
                after_pass=_to_bool(crow.passed),
                before_score=None,  # score lives on EvalMetricResult, not EvalRunResult
                after_score=None,
                is_new_case=is_new,
                had_error=bool(had_error),
            )
            cases.append(effect)

        # Any baseline cases not in candidate become "new" for the candidate side
        # (but since we iterate candidate first, remaining baseline are orphans)
        return cases

    async def _load_run_results(
        self, tenant_id: str, eval_run_id: str,
    ) -> Sequence[EvalRunResult]:
        result = await self.db.execute(
            select(EvalRunResult).where(
                EvalRunResult.tenant_id == tenant_id,
                EvalRunResult.eval_run_id == eval_run_id,
            )
        )
        return result.scalars().all()

    async def _evaluate_hard_gates(
        self, tenant_id: str, cases: Sequence[CaseEffect],
    ) -> dict[str, bool]:
        """Evaluate hard gates from case classifications.

        Returns a dict of gate_name → passed (True = no violation).
        """
        # 自动安全闸门尚未实现：当前 case 数据（CaseEffect）只含任务级
        # pass/fail/regression，不含租户泄露 / 事实错误 / 不安全 / 幻觉等安全
        # 信号，无法从这里自动判定。默认全 pass（不阻断发布），调用方可通过
        # compute_effect(hard_gates=...) 显式覆盖。接入安全评估后在此补实现。
        logger.warning(
            "Hard-gate auto-evaluation is NOT implemented (tenant=%s); "
            "defaulting all gates to pass. Pass hard_gates=... to compute_effect "
            "to enforce gates explicitly.",
            tenant_id,
        )
        return {
            "tenant_leakage": True,
            "critical_fact_error": True,
            "unsafe_response": True,
            "increased_fabrication": True,
        }
