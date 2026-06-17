"""Run Tracer 单元测试。"""

from __future__ import annotations

import json
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_run import AgentRun, AgentRunStep
from sales_agent.services.run_tracer import RunTracer


class TestRunTracer:
    @pytest.mark.asyncio
    async def test_start_run_creates_record(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """start_run 应创建 AgentRun 记录。"""
        tracer = RunTracer(db_session)
        run_id = await tracer.start_run(
            tenant_id=sample_tenant,
            conversation_id="conv_001",
            user_id="user_001",
        )
        assert run_id is not None
        assert tracer.run_id == run_id

        # 验证 DB 记录
        stmt = select(AgentRun).where(AgentRun.id == run_id)
        result = await db_session.execute(stmt)
        run = result.scalar_one()
        assert run.tenant_id == sample_tenant
        assert run.conversation_id == "conv_001"
        assert run.status == "running"

    @pytest.mark.asyncio
    async def test_record_step_creates_ordered_steps(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """record_step 应创建有序步骤。"""
        tracer = RunTracer(db_session)
        run_id = await tracer.start_run(
            tenant_id=sample_tenant,
            conversation_id="conv_002",
            user_id="user_001",
        )

        await tracer.record_step("validation", latency_ms=5)
        await tracer.record_step("routing", latency_ms=10, metadata={"task_type": "knowledge_qa"})
        await tracer.record_step("generation", latency_ms=200)

        # 验证步骤
        stmt = (
            select(AgentRunStep)
            .where(AgentRunStep.agent_run_id == run_id)
            .order_by(AgentRunStep.step_order)
        )
        result = await db_session.execute(stmt)
        steps = list(result.scalars().all())

        assert len(steps) == 3
        assert steps[0].step_name == "validation"
        assert steps[0].step_order == 0
        assert steps[0].latency_ms == 5
        assert steps[1].step_name == "routing"
        assert steps[1].step_order == 1
        assert json.loads(steps[1].metadata_json)["task_type"] == "knowledge_qa"
        assert steps[2].step_name == "generation"
        assert steps[2].step_order == 2

    @pytest.mark.asyncio
    async def test_complete_run(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """complete_run 应更新 run 状态和延迟。"""
        tracer = RunTracer(db_session)
        run_id = await tracer.start_run(
            tenant_id=sample_tenant,
            conversation_id="conv_003",
            user_id="user_001",
        )
        await tracer.record_step("validation", latency_ms=5)
        await tracer.complete_run(total_latency_ms=500, route_confidence=0.95)

        stmt = select(AgentRun).where(AgentRun.id == run_id)
        run = (await db_session.execute(stmt)).scalar_one()
        assert run.status == "completed"
        assert run.total_latency_ms == 500
        assert run.route_confidence == 0.95

    @pytest.mark.asyncio
    async def test_fail_run(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """fail_run 应标记 run 为 failed 并记录错误。"""
        tracer = RunTracer(db_session)
        run_id = await tracer.start_run(
            tenant_id=sample_tenant,
            conversation_id="conv_004",
            user_id="user_001",
        )
        await tracer.record_step("validation", latency_ms=5)
        await tracer.record_step("generation", status="failed", error_summary="Model timeout")
        await tracer.fail_run("Model timeout after 30s")

        stmt = select(AgentRun).where(AgentRun.id == run_id)
        run = (await db_session.execute(stmt)).scalar_one()
        assert run.status == "failed"
        error_data = json.loads(run.error_json)
        assert "Model timeout" in error_data["error_summary"]

    @pytest.mark.asyncio
    async def test_sanitize_metadata(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """敏感字段应被清理。"""
        tracer = RunTracer(db_session)
        run_id = await tracer.start_run(
            tenant_id=sample_tenant,
            conversation_id="conv_005",
            user_id="user_001",
        )
        await tracer.record_step(
            "routing",
            latency_ms=10,
            metadata={
                "task_type": "knowledge_qa",
                "api_key": "sk-secret-123",
                "nested": {"token": "secret-value", "safe": "ok"},
            },
        )

        stmt = (
            select(AgentRunStep)
            .where(AgentRunStep.agent_run_id == run_id)
        )
        step = (await db_session.execute(stmt)).scalar_one()
        meta = json.loads(step.metadata_json)
        assert meta["api_key"] == "[REDACTED]"
        assert meta["nested"]["token"] == "[REDACTED]"
        assert meta["nested"]["safe"] == "ok"
        assert meta["task_type"] == "knowledge_qa"
