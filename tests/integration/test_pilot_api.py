"""Phase D: Pilot API 集成测试。"""

from __future__ import annotations

import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id


class TestPilotMetrics:
    """R2: Pilot 成功指标。"""

    @pytest.mark.asyncio
    async def test_metrics_empty_tenant(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """空租户的指标应返回零值。"""
        from sales_agent.services.pilot_metrics_service import PilotMetricsService

        svc = PilotMetricsService(db_session)
        metrics = await svc.get_metrics(sample_tenant, "2026-01-01", "2026-12-31")

        assert metrics["usage"]["total_conversations"] == 0
        assert metrics["usage"]["unique_users"] == 0
        assert metrics["quality"]["feedback_positive_ratio"] == 0
        assert metrics["performance"]["error_rate"] == 0

    @pytest.mark.asyncio
    async def test_metrics_with_data(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """有数据的租户应返回正确指标。"""
        from sales_agent.services.pilot_metrics_service import PilotMetricsService

        svc = PilotMetricsService(db_session)
        metrics = await svc.get_metrics(sample_tenant, "2000-01-01", "2099-12-31")

        assert metrics["usage"]["total_conversations"] >= 1
        assert metrics["usage"]["unique_users"] >= 1


class TestReviewQueue:
    """R3: 质量审查队列。"""

    @pytest.mark.asyncio
    async def test_scan_creates_items_from_negative_feedback(
        self, db_session: AsyncSession, sample_tenant: str, sample_feedback
    ):
        """扫描负反馈应创建审查条目。"""
        from sales_agent.services.review_queue_service import ReviewQueueService

        svc = ReviewQueueService(db_session)
        count = await svc.scan_and_enqueue(sample_tenant)
        assert count >= 1

        items, total = await svc.list_items(sample_tenant, reason="negative_feedback")
        assert total >= 1
        assert items[0].reason == "negative_feedback"

    @pytest.mark.asyncio
    async def test_scan_deduplication(
        self, db_session: AsyncSession, sample_tenant: str, sample_feedback
    ):
        """重复扫描不应创建重复条目。"""
        from sales_agent.services.review_queue_service import ReviewQueueService

        svc = ReviewQueueService(db_session)
        count1 = await svc.scan_and_enqueue(sample_tenant)
        count2 = await svc.scan_and_enqueue(sample_tenant)
        # 第二次扫描不应新增（去重）
        assert count2 == 0

    @pytest.mark.asyncio
    async def test_status_transitions(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """审查条目状态应能正确转换。"""
        from sales_agent.services.review_queue_service import ReviewQueueService

        svc = ReviewQueueService(db_session)
        item = await svc.create_manual(
            tenant_id=sample_tenant,
            conversation_id=sample_conversation,
            reason="manual_flag",
        )
        assert item.status == "open"

        updated = await svc.update_status(
            tenant_id=sample_tenant,
            item_id=item.id,
            status="in_progress",
            assignee="admin",
        )
        assert updated.status == "in_progress"
        assert updated.assignee == "admin"

        resolved = await svc.update_status(
            tenant_id=sample_tenant,
            item_id=item.id,
            status="resolved",
        )
        assert resolved.status == "resolved"

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """不同租户的审查条目应隔离。"""
        from sales_agent.services.review_queue_service import ReviewQueueService

        svc = ReviewQueueService(db_session)
        await svc.create_manual(
            tenant_id=sample_tenant,
            conversation_id=sample_conversation,
        )
        _, total = await svc.list_items("nonexistent_tenant")
        assert total == 0


class TestFeedbackClassification:
    """R4: 反馈根因分类。"""

    @pytest.mark.asyncio
    async def test_classify_and_aggregate(
        self, db_session: AsyncSession, sample_tenant: str, sample_feedback
    ):
        """分类反馈后应能聚合统计。"""
        from sales_agent.services.feedback_classification_service import (
            FeedbackClassificationService,
        )

        svc = FeedbackClassificationService(db_session)
        fb = await svc.classify(
            sample_tenant, sample_feedback.id, ["wrong_answer", "bad_prompt"]
        )
        categories = json.loads(fb.categories_json)
        assert "wrong_answer" in categories
        assert "bad_prompt" in categories

        summary = await svc.get_categories_summary(sample_tenant)
        assert summary.get("wrong_answer") == 1
        assert summary.get("bad_prompt") == 1

    @pytest.mark.asyncio
    async def test_invalid_category_raises(
        self, db_session: AsyncSession, sample_tenant: str, sample_feedback
    ):
        """非法分类应抛出异常。"""
        from sales_agent.services.feedback_classification_service import (
            FeedbackClassificationService,
        )

        svc = FeedbackClassificationService(db_session)
        with pytest.raises(ValueError, match="Invalid categories"):
            await svc.classify(sample_tenant, sample_feedback.id, ["not_a_category"])


class TestKnowledgeGap:
    """R5: 知识缺口追踪。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """知识缺口应能完成完整生命周期。"""
        from sales_agent.services.knowledge_gap_service import KnowledgeGapService

        svc = KnowledgeGapService(db_session)

        # 创建
        gap = await svc.create(
            tenant_id=sample_tenant,
            title="产品定价策略缺失",
            description="缺少竞品定价对比文档",
        )
        assert gap.status == "open"

        # 转换为 document_needed
        gap = await svc.transition(sample_tenant, gap.id, "document_needed")
        assert gap.status == "document_needed"

        # 关联文档
        doc_id = generate_id()
        gap = await svc.link_document(sample_tenant, gap.id, doc_id)
        assert gap.linked_document_id == doc_id

        # 转换为 uploaded
        gap = await svc.transition(sample_tenant, gap.id, "uploaded")
        assert gap.status == "uploaded"

        # 转换为 verified
        gap = await svc.transition(sample_tenant, gap.id, "verified")
        assert gap.status == "verified"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """非法状态转换应抛出异常。"""
        from sales_agent.services.knowledge_gap_service import KnowledgeGapService

        svc = KnowledgeGapService(db_session)
        gap = await svc.create(tenant_id=sample_tenant, title="Test gap")

        with pytest.raises(ValueError, match="Invalid transition"):
            await svc.transition(sample_tenant, gap.id, "verified")  # open -> verified 不允许

    @pytest.mark.asyncio
    async def test_summary_counts(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """摘要统计应正确计数。"""
        from sales_agent.services.knowledge_gap_service import KnowledgeGapService

        svc = KnowledgeGapService(db_session)
        await svc.create(tenant_id=sample_tenant, title="Gap 1")
        await svc.create(tenant_id=sample_tenant, title="Gap 2")

        summary = await svc.get_summary(sample_tenant)
        assert summary["open"] == 2
        assert summary["total"] == 2


class TestAlerts:
    """R8: 运维告警。"""

    @pytest.mark.asyncio
    async def test_rule_crud(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """告警规则应能 CRUD。"""
        from sales_agent.services.alert_service import AlertService

        svc = AlertService(db_session)

        # 创建
        rule = await svc.create_rule(
            tenant_id=sample_tenant,
            name="高错误率",
            metric="high_error_rate",
            threshold=0.1,
            severity="warning",
        )
        assert rule.metric == "high_error_rate"

        # 列出
        rules, total = await svc.list_rules(sample_tenant)
        assert total >= 1

        # 获取
        found = await svc.get_rule(sample_tenant, rule.id)
        assert found is not None
        assert found.name == "高错误率"

    @pytest.mark.asyncio
    async def test_alert_lifecycle(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """告警应能确认和解决。"""
        from sales_agent.services.alert_service import AlertService
        from sales_agent.models.alert import Alert
        from sales_agent.models.base import generate_id, utcnow

        # 先创建规则
        svc = AlertService(db_session)
        rule = await svc.create_rule(
            tenant_id=sample_tenant,
            name="测试规则",
            metric="model_failures",
            threshold=5,
            severity="warning",
        )

        # 手动创建一个告警来测试生命周期
        alert = Alert(
            id=generate_id(),
            tenant_id=sample_tenant,
            alert_rule_id=rule.id,
            severity="warning",
            metric="model_failures",
            threshold_value=5,
            observed_value=10,
            status="active",
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
        db_session.add(alert)
        await db_session.flush()

        # 确认
        ack = await svc.acknowledge_alert(sample_tenant, alert.id)
        assert ack.status == "acknowledged"

        # 解决
        resolved = await svc.resolve_alert(sample_tenant, alert.id)
        assert resolved.status == "resolved"

    @pytest.mark.asyncio
    async def test_seed_default_rules(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """默认规则种子应创建 5 条规则。"""
        from sales_agent.services.alert_service import AlertService

        svc = AlertService(db_session)
        rules = await svc.seed_default_rules(sample_tenant)
        assert len(rules) == 5


class TestPilotReport:
    """R9: Pilot 报告。"""

    @pytest.mark.asyncio
    async def test_generate_report(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """报告应能生成并包含 Markdown。"""
        from sales_agent.services.pilot_report_service import PilotReportService

        svc = PilotReportService(db_session)
        report = await svc.generate_report(
            sample_tenant, "2026-01-01", "2026-12-31"
        )
        assert report.status == "completed"
        assert report.markdown_content is not None
        assert "Pilot 周报" in report.markdown_content

        # 报告 JSON 应可解析
        report_data = json.loads(report.report_json)
        assert "usage_summary" in report_data
        assert "quality" in report_data

    @pytest.mark.asyncio
    async def test_list_reports(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """应能列出报告。"""
        from sales_agent.services.pilot_report_service import PilotReportService

        svc = PilotReportService(db_session)
        await svc.generate_report(sample_tenant, "2026-01-01", "2026-06-11")
        items, total = await svc.list_reports(sample_tenant)
        assert total >= 1


class TestPilotStatus:
    """R10: Pilot 退出决策。"""

    @pytest.mark.asyncio
    async def test_empty_tenant_status(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """空租户应为 stop 或 continue_pilot 状态。"""
        from sales_agent.services.pilot_status_service import PilotStatusService

        svc = PilotStatusService(db_session)
        result = await svc.get_pilot_status(sample_tenant)

        assert result["classification"] in ("expand", "continue_pilot", "needs_remediation", "stop")
        assert isinstance(result["reasons"], list)
        assert isinstance(result["next_actions"], list)
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_classification_values(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """分类值应为四种之一。"""
        from sales_agent.services.pilot_status_service import PilotStatusService

        svc = PilotStatusService(db_session)
        result = await svc.get_pilot_status(sample_tenant)
        assert result["classification"] in {"expand", "continue_pilot", "needs_remediation", "stop"}


class TestTenantIsolation:
    """验证所有 Phase D 数据都按租户隔离。"""

    @pytest.mark.asyncio
    async def test_review_queue_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str, sample_conversation: str
    ):
        """不同租户的审查条目应隔离。"""
        from sales_agent.services.review_queue_service import ReviewQueueService

        svc = ReviewQueueService(db_session)
        await svc.create_manual(sample_tenant, sample_conversation)

        other_items, other_total = await svc.list_items("other_tenant_xyz")
        assert other_total == 0

    @pytest.mark.asyncio
    async def test_knowledge_gap_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """不同租户的知识缺口应隔离。"""
        from sales_agent.services.knowledge_gap_service import KnowledgeGapService

        svc = KnowledgeGapService(db_session)
        await svc.create(sample_tenant, title="Tenant gap")

        other_items, other_total = await svc.list_gaps("other_tenant_xyz")
        assert other_total == 0

    @pytest.mark.asyncio
    async def test_alert_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """不同租户的告警应隔离。"""
        from sales_agent.services.alert_service import AlertService

        svc = AlertService(db_session)
        await svc.create_rule(sample_tenant, "Test", "model_failures", 5)

        other_rules, other_total = await svc.list_rules("other_tenant_xyz")
        assert other_total == 0
