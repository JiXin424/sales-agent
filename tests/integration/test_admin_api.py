"""Admin API 集成测试。"""

from __future__ import annotations

import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_run import AgentRun, AgentRunStep
from sales_agent.models.feedback import Feedback
from sales_agent.models.base import generate_id


class TestAdminAPIRoutes:
    """验证 Admin API 路由和数据查询。"""

    @pytest.mark.asyncio
    async def test_conversation_query_tenant_scoped(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """会话查询应按租户隔离。"""
        from sales_agent.models.conversation import Conversation
        from sqlalchemy import select, func

        # 查询测试租户的会话数量
        stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.tenant_id == sample_tenant)
        )
        count = (await db_session.execute(stmt)).scalar()
        assert count >= 1  # sample_conversation 已创建

        # 其他租户应为 0
        other_stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.tenant_id == "nonexistent_tenant")
        )
        other_count = (await db_session.execute(other_stmt)).scalar()
        assert other_count == 0

    @pytest.mark.asyncio
    async def test_run_trace_with_steps(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """完整的 run trace 应包含有序步骤。"""
        run_id = generate_id()
        run = AgentRun(
            id=run_id,
            tenant_id=sample_tenant,
            conversation_id=sample_conversation,
            user_id="user_001",
            task_type="knowledge_qa",
            path="standard",
            status="completed",
            total_latency_ms=500,
            route_confidence=0.9,
        )
        db_session.add(run)

        steps = [
            ("validation", 0, "completed", 5),
            ("tenant_resolve", 1, "completed", 10),
            ("routing", 2, "completed", 50, {"task_type": "knowledge_qa"}),
            ("retrieval", 3, "completed", 100, {"called": True, "source_count": 3}),
            ("generation", 4, "completed", 300),
            ("risk_check", 5, "completed", 20, {"level": "none", "action": "allow"}),
            ("logging", 6, "completed", 15),
        ]
        for step_data in steps:
            name, order, status, latency = step_data[:4]
            metadata = step_data[4] if len(step_data) > 4 else {}
            step = AgentRunStep(
                id=generate_id(),
                agent_run_id=run_id,
                tenant_id=sample_tenant,
                step_name=name,
                step_order=order,
                status=status,
                latency_ms=latency,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )
            db_session.add(step)

        await db_session.flush()

        # 查询步骤
        from sqlalchemy import select
        stmt = (
            select(AgentRunStep)
            .where(AgentRunStep.agent_run_id == run_id)
            .order_by(AgentRunStep.step_order)
        )
        result = await db_session.execute(stmt)
        db_steps = list(result.scalars().all())

        assert len(db_steps) == 7
        assert db_steps[0].step_name == "validation"
        assert db_steps[3].step_name == "retrieval"
        assert db_steps[3].latency_ms == 100
        retrieval_meta = json.loads(db_steps[3].metadata_json)
        assert retrieval_meta["source_count"] == 3

    @pytest.mark.asyncio
    async def test_failed_run_trace(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """失败的 run 应记录错误信息。"""
        run_id = generate_id()
        run = AgentRun(
            id=run_id,
            tenant_id=sample_tenant,
            conversation_id="conv_fail",
            user_id="user_001",
            task_type="knowledge_qa",
            status="failed",
            error_json=json.dumps({"error_summary": "Model timeout"}, ensure_ascii=False),
        )
        db_session.add(run)

        step = AgentRunStep(
            id=generate_id(),
            agent_run_id=run_id,
            tenant_id=sample_tenant,
            step_name="generation",
            step_order=3,
            status="failed",
            error_summary="Model timeout after 30s",
        )
        db_session.add(step)
        await db_session.flush()

        from sqlalchemy import select
        stmt = select(AgentRunStep).where(AgentRunStep.agent_run_id == run_id)
        db_step = (await db_session.execute(stmt)).scalar_one()
        assert db_step.status == "failed"
        assert db_step.error_summary == "Model timeout after 30s"

    @pytest.mark.asyncio
    async def test_feedback_query_tenant_scoped(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """反馈查询应按租户隔离。"""
        fb = Feedback(
            tenant_id=sample_tenant,
            conversation_id=sample_conversation,
            user_id="user_001",
            rating="up",
            feedback_text="很有帮助",
        )
        db_session.add(fb)
        await db_session.flush()

        from sqlalchemy import select, func
        stmt = (
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.tenant_id == sample_tenant)
        )
        count = (await db_session.execute(stmt)).scalar()
        assert count >= 1

        # 其他租户
        other_stmt = (
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.tenant_id == "other_tenant")
        )
        other_count = (await db_session.execute(other_stmt)).scalar()
        assert other_count == 0

    @pytest.mark.asyncio
    async def test_model_call_log_tenant_scoped(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """模型调用日志应按租户隔离。"""
        from sales_agent.models.model_call_log import ModelCallLog
        from sqlalchemy import select, func

        log = ModelCallLog(
            tenant_id=sample_tenant,
            provider="openai_compatible",
            request_type="chat",
            status="success",
            latency_ms=200,
        )
        db_session.add(log)
        await db_session.flush()

        stmt = (
            select(func.count())
            .select_from(ModelCallLog)
            .where(ModelCallLog.tenant_id == sample_tenant)
        )
        count = (await db_session.execute(stmt)).scalar()
        assert count >= 1

    @pytest.mark.asyncio
    async def test_pagination_pattern(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """分页查询应正确返回 total/limit/offset。"""
        from sales_agent.models.feedback import Feedback
        from sqlalchemy import select, func

        # 创建多条反馈
        for i in range(5):
            fb = Feedback(
                tenant_id=sample_tenant,
                conversation_id=sample_conversation,
                user_id=f"user_{i}",
                rating="up" if i % 2 == 0 else "down",
            )
            db_session.add(fb)
        await db_session.flush()

        # Count
        total_stmt = (
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.tenant_id == sample_tenant)
        )
        total = (await db_session.execute(total_stmt)).scalar()
        assert total >= 5

        # Page 1
        page_stmt = (
            select(Feedback)
            .where(Feedback.tenant_id == sample_tenant)
            .order_by(Feedback.created_at.desc())
            .limit(3)
            .offset(0)
        )
        page1 = (await db_session.execute(page_stmt)).scalars().all()
        assert len(page1) == 3

    # --- Phase B: Workflow Metrics ---

    @pytest.mark.asyncio
    async def test_workflow_metrics_task_type_distribution(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """工作流指标应正确聚合任务类型分布。"""
        from sales_agent.models.conversation import Conversation
        from sqlalchemy import func, select

        workflow_types = [
            "visit_preparation", "follow_up_planning",
            "customer_context_summary", "deal_advancement", "conversation_scoring",
        ]
        for i, task_type in enumerate(workflow_types):
            conv = Conversation(
                id=generate_id(),
                tenant_id=sample_tenant,
                user_id=f"user_{i}",
                channel="local",
                message=f"测试 {task_type}",
                task_type=task_type,
                status="completed",
            )
            db_session.add(conv)
        await db_session.flush()

        # Query task type distribution
        stmt = (
            select(Conversation.task_type, func.count().label("count"))
            .where(
                Conversation.tenant_id == sample_tenant,
                Conversation.task_type.in_(workflow_types),
            )
            .group_by(Conversation.task_type)
        )
        rows = (await db_session.execute(stmt)).all()
        dist = {row[0]: row[1] for row in rows}
        assert len(dist) == 5
        for task_type in workflow_types:
            assert dist.get(task_type, 0) == 1

    @pytest.mark.asyncio
    async def test_workflow_metrics_stage_distribution(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """工作流指标应正确聚合销售阶段分布。"""
        from sales_agent.models.conversation import Conversation
        from sqlalchemy import func, select

        stages = ["visit_preparation", "proposal", "deal_closing"]
        for i, stage in enumerate(stages):
            conv = Conversation(
                id=generate_id(),
                tenant_id=sample_tenant,
                user_id=f"user_stage_{i}",
                channel="local",
                message=f"测试 stage {stage}",
                task_type="visit_preparation",
                status="completed",
                stage=stage,
            )
            db_session.add(conv)
        await db_session.flush()

        stmt = (
            select(Conversation.stage, func.count().label("count"))
            .where(
                Conversation.tenant_id == sample_tenant,
                Conversation.stage.isnot(None),
            )
            .group_by(Conversation.stage)
        )
        rows = (await db_session.execute(stmt)).all()
        stage_dist = {row[0]: row[1] for row in rows}
        assert len(stage_dist) == 3

    @pytest.mark.asyncio
    async def test_workflow_metrics_low_score_detection(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """工作流指标应识别低分对话。"""
        from sales_agent.services.task_router import DEAL_ADVANCEMENT
        from sales_agent.api.routes.admin import _extract_overall_score

        # Test the score extraction helper
        answer_low = {
            "summary": "评分较低",
            "sections": [
                {"title": "总分", "content": "45/100（加权计算）"},
            ],
        }
        answer_high = {
            "summary": "评分较高",
            "sections": [
                {"title": "总分", "content": "85/100（加权计算）"},
            ],
        }
        assert _extract_overall_score(answer_low) == 45
        assert _extract_overall_score(answer_high) == 85

    @pytest.mark.asyncio
    async def test_workflow_metrics_missing_fields_extraction(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """工作流指标应正确提取常见缺失字段。"""
        from sales_agent.api.routes.admin import _extract_missing_fields

        answer = {
            "summary": "客户上下文整理",
            "sections": [
                {"title": "缺失信息", "content": "1. 预算范围\n2. 决策人确认\n3. 竞品对比情况"},
            ],
        }
        fields = _extract_missing_fields(answer)
        assert len(fields) == 3
        assert "预算范围" in fields
        assert "决策人确认" in fields

    @pytest.mark.asyncio
    async def test_workflow_feedback_by_task(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """工作流反馈应按任务类型聚合。"""
        from sales_agent.models.conversation import Conversation

        # Create a workflow conversation
        conv = Conversation(
            id=generate_id(),
            tenant_id=sample_tenant,
            user_id="user_feedback",
            channel="local",
            message="测试反馈",
            task_type="visit_preparation",
            status="completed",
        )
        db_session.add(conv)
        await db_session.flush()

        fb = Feedback(
            tenant_id=sample_tenant,
            conversation_id=conv.id,
            user_id="user_feedback",
            rating="up",
            feedback_text="很有帮助",
        )
        db_session.add(fb)
        await db_session.flush()

        from sqlalchemy import func, select
        stmt = (
            select(
                Conversation.task_type,
                Feedback.rating,
                func.count().label("count"),
            )
            .join(Feedback, Feedback.conversation_id == Conversation.id)
            .where(
                Conversation.tenant_id == sample_tenant,
                Conversation.task_type == "visit_preparation",
            )
            .group_by(Conversation.task_type, Feedback.rating)
        )
        rows = (await db_session.execute(stmt)).all()
        assert len(rows) == 1
        assert rows[0][0] == "visit_preparation"
        assert rows[0][1] == "up"
        assert rows[0][2] == 1
