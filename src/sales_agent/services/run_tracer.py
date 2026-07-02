"""Agent 运行追踪服务：记录每次 chat 请求的运行步骤和延迟。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_run import AgentRun, AgentRunStep
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


class RunTracer:
    """Agent 运行追踪器。

    在 ChatPipeline 中使用：

    .. code-block:: python

        tracer = RunTracer(db)
        run_id = await tracer.start_run(tenant_id, conv_id, user_id)
        await tracer.record_step("validation", latency_ms=5)
        ...
        await tracer.complete_run(total_latency_ms=1200)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._run_id: str | None = None
        self._tenant_id: str | None = None
        self._agent_id: str | None = None
        self._step_order: int = 0

    @property
    def run_id(self) -> str | None:
        """当前 run 的 ID。"""
        return self._run_id

    async def start_run(
        self,
        tenant_id: str,
        conversation_id: str,
        user_id: str,
        task_type: str | None = None,
        path: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """创建一个 AgentRun 记录，返回 run_id。"""
        run_id = generate_id()
        run = AgentRun(
            id=run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            task_type=task_type,
            path=path,
            status="running",
        )
        self.db.add(run)
        await self.db.flush()

        self._run_id = run_id
        self._tenant_id = tenant_id
        self._agent_id = agent_id
        self._step_order = 0
        return run_id

    async def record_step(
        self,
        step_name: str,
        status: str = "completed",
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        """记录一个运行步骤。

        step_order 自动递增。
        metadata 中的敏感信息（API key 等）不应被存储。
        """
        if self._run_id is None:
            logger.warning("record_step called without an active run.")
            return

        # 清理 metadata：移除可能的敏感字段
        safe_metadata = _sanitize_metadata(metadata) if metadata else {}

        step = AgentRunStep(
            id=generate_id(),
            agent_run_id=self._run_id,
            tenant_id=self._tenant_id,
            agent_id=self._agent_id,
            step_name=step_name,
            step_order=self._step_order,
            status=status,
            latency_ms=latency_ms,
            metadata_json=json.dumps(safe_metadata, ensure_ascii=False),
            error_summary=error_summary,
        )
        self.db.add(step)
        await self.db.flush()
        self._step_order += 1

    async def complete_run(
        self,
        total_latency_ms: int,
        route_confidence: float | None = None,
    ) -> None:
        """标记 run 为 completed。"""
        if self._run_id is None:
            return

        stmt = select(AgentRun).where(AgentRun.id == self._run_id)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()
        if run is not None:
            run.status = "completed"
            run.total_latency_ms = total_latency_ms
            run.route_confidence = route_confidence
            await self.db.flush()

    async def fail_run(self, error_summary: str) -> None:
        """标记 run 为 failed。"""
        if self._run_id is None:
            return

        stmt = select(AgentRun).where(AgentRun.id == self._run_id)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()
        if run is not None:
            run.status = "failed"
            run.error_json = json.dumps(
                {"error_summary": error_summary}, ensure_ascii=False
            )
            await self.db.flush()


# 需要从 metadata 中移除的敏感字段名
_SENSITIVE_KEYS = frozenset({
    "api_key", "api_key_ref", "api_key_env", "authorization",
    "token", "secret", "password", "credential",
})


    # ── Route and retrieval trace (knowledge iteration) ──────────────────

    async def record_route_trace(
        self,
        router_type: str,
        task_type: str,
        confidence: float,
        llm_called: bool,
        needs_retrieval: bool,
        decision_reason: str = "",
        route_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record route decision evidence for attribution.

        Persisted as a step named ``route_trace`` with full decision metadata.
        """
        meta = {
            "router_type": router_type,
            "task_type": task_type,
            "confidence": confidence,
            "llm_called": llm_called,
            "needs_retrieval": needs_retrieval,
            "decision_reason": decision_reason,
        }
        if route_metadata:
            safe = _sanitize_metadata(route_metadata)
            meta.update(safe)
        await self.record_step("route_trace", metadata=meta)

    async def record_retrieval_trace(
        self,
        original_query: str,
        rewritten_queries: list[str] | None = None,
        top_k: int = 5,
        candidate_k: int = 30,
        vector_weight: float | None = None,
        keyword_weight: float | None = None,
        rrf_constant: int | None = None,
        retrieval_triggered: bool = True,
        skip_reason: str | None = None,
        trace_hits: list[dict[str, Any]] | None = None,
        retrieval_latency_ms: float = 0.0,
    ) -> None:
        """Record ranked retrieval evidence for attribution.

        Each hit dict must include: channel, channel_rank, channel_score,
        final_rank, final_score, selected_for_context, chunk_id, document_id.
        """
        meta = {
            "original_query": original_query,
            "rewritten_queries": rewritten_queries or [],
            "top_k": top_k,
            "candidate_k": candidate_k,
            "vector_weight": vector_weight,
            "keyword_weight": keyword_weight,
            "rrf_constant": rrf_constant,
            "retrieval_triggered": retrieval_triggered,
            "skip_reason": skip_reason,
            "trace_hits": trace_hits or [],
            "retrieval_latency_ms": retrieval_latency_ms,
        }
        await self.record_step("retrieval_trace", metadata=_sanitize_metadata(meta))


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """移除 metadata 中的敏感字段，包括嵌套 list 和 dict。"""
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in _SENSITIVE_KEYS:
            safe[key] = "[REDACTED]"
        elif isinstance(value, dict):
            safe[key] = _sanitize_metadata(value)
        elif isinstance(value, list):
            safe[key] = [
                _sanitize_metadata(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            safe[key] = value
    return safe
