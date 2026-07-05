"""Evaluation runner — executes eval cases through the Graph path (same as DingTalk Stream).

Replaces the placeholder stubs in worker.py with real evaluation logic.
Each run goes through: graph.ainvoke() → routing(LLM) → ontology+RAG parallel →
generation → result capture.

Usage:
    runner = EvalRunner(db_session, settings)
    eval_run = await runner.run_suite(eval_suite_id, tenant_id, agent_id)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sales_agent.core.config import Settings
from sales_agent.models.base import generate_id
from sales_agent.models.eval import EvalCase, EvalRun, EvalRunResult, EvalSuite

logger = logging.getLogger(__name__)

# Max concurrent graph invocations per batch
_MAX_CONCURRENCY = 3
# Max total questions per run (0 = unlimited)
_DEFAULT_MAX_QUESTIONS = 0


@dataclass
class EvalRunSummary:
    """Result summary for a completed evaluation run."""

    run_id: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_latency_ms: int = 0
    avg_latency_ms: int = 0
    errors: list[str] = field(default_factory=list)


class EvalRunner:
    """Execute evaluation suites through the production Graph path."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        max_questions: int = _DEFAULT_MAX_QUESTIONS,
    ) -> None:
        self._db = db
        self._settings = settings
        self._max_questions = max_questions

    async def run_suite(
        self,
        eval_suite_id: str,
        tenant_id: str,
        agent_id: str | None = None,
        *,
        iteration_id: str | None = None,
        candidate_id: str | None = None,
        run_type: str = "fixed",
    ) -> EvalRunSummary:
        """Run all cases in an eval suite through the graph path.

        Args:
            eval_suite_id: The evaluation suite to run.
            tenant_id: Target tenant.
            agent_id: Optional agent ID.
            iteration_id: Optional iteration tracking.
            candidate_id: Optional candidate tracking.
            run_type: "fixed" | "targeted" | "exploration"

        Returns:
            EvalRunSummary with pass/fail counts and timing.
        """
        # 1. Load eval suite and cases
        suite = await self._db.scalar(
            select(EvalSuite).where(EvalSuite.id == eval_suite_id)
        )
        if suite is None:
            raise ValueError(f"EvalSuite not found: {eval_suite_id}")

        cases_query = (
            select(EvalCase)
            .where(
                EvalCase.tenant_id == tenant_id,
                EvalCase.eval_suite_id == eval_suite_id,
                EvalCase.quality_status != "rejected",
            )
            .order_by(EvalCase.case_id)
        )
        if self._max_questions > 0:
            cases_query = cases_query.limit(self._max_questions)

        result = await self._db.execute(cases_query)
        cases = result.scalars().all()

        if not cases:
            logger.warning("No eval cases found for suite=%s tenant=%s", eval_suite_id, tenant_id)
            return EvalRunSummary(run_id="", total=0)

        logger.info(
            "Starting eval run: suite=%s tenant=%s cases=%d",
            eval_suite_id, tenant_id, len(cases),
        )

        # 2. Create eval_run record
        run_id = generate_id()
        config_snapshot = json.dumps({
            "knowledge_engine": self._settings.ontology.knowledge_engine,
            "hybrid_retrieval": self._settings.ontology.hybrid_retrieval,
            "parallel_enabled": getattr(self._settings.retrieval, "parallel_enabled", True),
            "top_k": self._settings.retrieval.top_k,
            "mode": self._settings.retrieval.mode,
        }, ensure_ascii=False)

        eval_run = EvalRun(
            id=run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            eval_suite_id=eval_suite_id,
            status="running",
            total_cases=len(cases),
            passed=0,
            failed=0,
            skipped=0,
            config_snapshot_json=config_snapshot,
            iteration_id=iteration_id,
            candidate_id=candidate_id,
            run_type=run_type,
            started_at=_now(),
        )
        self._db.add(eval_run)
        await self._db.flush()

        # 3. Resolve model provider once
        from sales_agent.services.tenant_resolver import TenantResolver
        resolver = TenantResolver(self._db)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        # 4. Run each case through the graph
        total_start = time.monotonic()
        passed = 0
        failed = 0
        errors: list[str] = []

        import asyncio
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def run_one(case: EvalCase) -> dict:
            async with semaphore:
                return await self._run_single_case(
                    case, tenant_id, agent_id, run_id, model_provider,
                )

        # Execute cases with concurrency limit
        results = []
        for i in range(0, len(cases), _MAX_CONCURRENCY):
            batch = cases[i : i + _MAX_CONCURRENCY]
            batch_results = await asyncio.gather(
                *[run_one(c) for c in batch], return_exceptions=True,
            )
            for j, r in enumerate(batch_results):
                if isinstance(r, Exception):
                    errors.append(f"{batch[j].case_id}: {r}")
                    failed += 1
                elif r.get("passed") == "fail":
                    failed += 1
                else:
                    passed += 1
                results.append(r)

            # Commit periodically to avoid losing results
            await self._db.flush()

        total_latency_ms = int((time.monotonic() - total_start) * 1000)

        # 5. Update eval_run with final counts
        eval_run.status = "completed"
        eval_run.passed = passed
        eval_run.failed = failed
        eval_run.skipped = 0
        eval_run.completed_at = _now()
        if errors:
            eval_run.error_summary = json.dumps(errors[:20], ensure_ascii=False)
        await self._db.flush()

        summary = EvalRunSummary(
            run_id=run_id,
            total=len(cases),
            passed=passed,
            failed=failed,
            skipped=0,
            total_latency_ms=total_latency_ms,
            avg_latency_ms=total_latency_ms // max(len(cases), 1),
            errors=errors[:50],
        )

        logger.info(
            "Eval run complete: run=%s passed=%d failed=%d total=%d latency=%dms",
            run_id, passed, failed, len(cases), total_latency_ms,
        )
        return summary

    async def _run_single_case(
        self,
        case: EvalCase,
        tenant_id: str,
        agent_id: str | None,
        run_id: str,
        model_provider: Any,
    ) -> dict:
        """Run a single eval case through the graph and store the result."""
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore
        from sales_agent.graph.chat_graph import build_chat_graph_compiled

        conversation_id = f"eval_{run_id}_{case.case_id}"
        t_start = time.monotonic()

        try:
            checkpointer = InMemorySaver()
            store = InMemoryStore()
            graph = build_chat_graph_compiled(checkpointer=checkpointer, store=store)

            result = await graph.ainvoke(
                {
                    "tenant_id": tenant_id,
                    "user_id": "eval_runner",
                    "message": case.input_text,
                    "conversation_id": conversation_id,
                    "channel": "eval",
                    "agent_id": agent_id,
                },
                config={"configurable": {"thread_id": conversation_id}},
                context={
                    "db": self._db,
                    "chat_model": model_provider.chat,
                    "embedding_model": model_provider.embedding,
                },
                durability="sync",
            )

            latency_ms = int((time.monotonic() - t_start) * 1000)

            # Extract results from graph state
            answer = result.get("answer_dict") or result.get("final_answer") or {}
            sources = result.get("sources") or result.get("final_sources") or []
            task_type = result.get("task_type", "")
            usage = result.get("usage") or {}
            risk_result = result.get("risk_result") or {}

            # Simple pass/fail: has non-empty answer + retrieval triggered
            summary = answer.get("summary", "") if isinstance(answer, dict) else str(answer)
            has_answer = bool(summary and len(summary) > 20)
            retrieval_triggered = bool(result.get("needs_retrieval") or
                                       task_type == "knowledge_qa")

            # Check expected constraints
            must_include = _parse_json_list(case.must_include_json)
            must_not_include = _parse_json_list(case.must_not_include_json)
            content_checks: dict[str, bool] = {}

            if must_include:
                for keyword in must_include:
                    content_checks[f"includes:{keyword}"] = keyword in summary
            if must_not_include:
                for keyword in must_not_include:
                    content_checks[f"excludes:{keyword}"] = keyword not in summary

            all_checks_pass = all(content_checks.values()) if content_checks else has_answer
            passed = "pass" if (has_answer and all_checks_pass) else "fail"
            failure_reasons = []
            if not has_answer:
                failure_reasons.append("empty_or_short_answer")
            if not all_checks_pass:
                failure_reasons.extend(
                    k for k, v in content_checks.items() if not v
                )

            # Store result
            eval_result = EvalRunResult(
                id=generate_id(),
                tenant_id=tenant_id,
                eval_run_id=run_id,
                eval_case_id=case.id,
                conversation_id=conversation_id,
                passed=passed,
                actual_task_type=task_type,
                actual_risk_level=risk_result.get("level", "none"),
                route_match="match" if task_type == case.expected_task_type else "mismatch",
                content_checks_json=json.dumps(content_checks, ensure_ascii=False),
                failure_reasons_json=json.dumps(failure_reasons, ensure_ascii=False),
                latency_ms=latency_ms,
                actual_output_json=json.dumps({
                    "summary": summary[:2000],
                    "source_count": len(sources),
                }, ensure_ascii=False),
                route_type=result.get("path", ""),
                route_confidence=result.get("route_confidence"),
                retrieval_triggered=retrieval_triggered,
                source_count=len(sources),
                token_usage_json=json.dumps(usage, ensure_ascii=False),
            )
            self._db.add(eval_result)

            return {
                "case_id": case.case_id,
                "passed": passed,
                "latency_ms": latency_ms,
                "task_type": task_type,
                "sources": len(sources),
                "answer_len": len(summary),
            }

        except Exception as e:
            latency_ms = int((time.monotonic() - t_start) * 1000)
            logger.error("Eval case %s failed: %s", case.case_id, e)
            # Store error result
            eval_result = EvalRunResult(
                id=generate_id(),
                tenant_id=tenant_id,
                eval_run_id=run_id,
                eval_case_id=case.id,
                passed="error",
                failure_reasons_json=json.dumps([f"{type(e).__name__}: {str(e)[:200]}"]),
                latency_ms=latency_ms,
                error_code=f"{type(e).__name__}",
                content_checks_json="{}",
                actual_output_json="{}",
                token_usage_json="{}",
            )
            self._db.add(eval_result)
            raise


def _parse_json_list(raw: str) -> list[str]:
    """Parse a JSON array string into a list of strings."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _now() -> str:
    """ISO-format timestamp string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
