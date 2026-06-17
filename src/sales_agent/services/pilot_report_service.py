"""Pilot 报告生成服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id, utcnow
from sales_agent.models.pilot_report import PilotReport
from sales_agent.services.alert_service import AlertService
from sales_agent.services.feedback_classification_service import FeedbackClassificationService
from sales_agent.services.knowledge_gap_service import KnowledgeGapService
from sales_agent.services.pilot_metrics_service import PilotMetricsService

logger = logging.getLogger(__name__)


class PilotReportService:
    """Pilot 报告生成。

    用法::

        svc = PilotReportService(db)
        report = await svc.generate_report(tenant_id, "2026-06-04", "2026-06-11")
        md = report.markdown_content
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_report(
        self,
        tenant_id: str,
        start_date: str,
        end_date: str,
        report_type: str = "weekly",
    ) -> PilotReport:
        """生成 Pilot 报告。

        汇总指标、反馈、知识缺口、告警等数据，生成结构化 JSON 和 Markdown。
        """
        report = PilotReport(
            id=generate_id(),
            tenant_id=tenant_id,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            status="generating",
        )
        self.db.add(report)
        await self.db.flush()

        try:
            # 收集各维度数据
            metrics_svc = PilotMetricsService(self.db)
            metrics = await metrics_svc.get_metrics(tenant_id, start_date, end_date)

            feedback_svc = FeedbackClassificationService(self.db)
            feedback_summary = await feedback_svc.get_categories_summary(tenant_id)

            gap_svc = KnowledgeGapService(self.db)
            gap_summary = await gap_svc.get_summary(tenant_id)

            alert_svc = AlertService(self.db)
            _, active_alerts_count = await alert_svc.list_alerts(tenant_id, status="active")

            # 组装报告 JSON
            report_json = {
                "usage_summary": metrics.get("usage", {}),
                "distribution": metrics.get("distribution", {}),
                "quality": metrics.get("quality", {}),
                "performance": metrics.get("performance", {}),
                "feedback_categories": feedback_summary,
                "knowledge_gaps": gap_summary,
                "active_alerts_count": active_alerts_count,
                "period": {"start_date": start_date, "end_date": end_date},
            }

            report.report_json = json.dumps(report_json, ensure_ascii=False, indent=2)
            report.summary_json = json.dumps({
                "total_conversations": metrics.get("usage", {}).get("total_conversations", 0),
                "unique_users": metrics.get("usage", {}).get("unique_users", 0),
                "feedback_positive_ratio": metrics.get("quality", {}).get("feedback_positive_ratio", 0),
                "error_rate": metrics.get("performance", {}).get("error_rate", 0),
                "open_gaps": gap_summary.get("open", 0) + gap_summary.get("document_needed", 0),
                "active_alerts": active_alerts_count,
            }, ensure_ascii=False)

            # 渲染 Markdown
            report.markdown_content = self._render_markdown(report_json)
            report.status = "completed"

        except Exception as e:
            logger.error("Report generation failed: %s", e)
            report.status = "failed"
            report.metadata_json = json.dumps({"error": str(e)}, ensure_ascii=False)

        await self.db.flush()
        return report

    async def get_report(self, tenant_id: str, report_id: str) -> PilotReport | None:
        """获取报告。"""
        stmt = select(PilotReport).where(
            PilotReport.id == report_id,
            PilotReport.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_reports(
        self,
        tenant_id: str,
        report_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PilotReport], int]:
        """列出报告。"""
        conditions = [PilotReport.tenant_id == tenant_id]
        if report_type:
            conditions.append(PilotReport.report_type == report_type)

        total = (await self.db.execute(
            select(func.count()).select_from(PilotReport).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(PilotReport)
            .where(*conditions)
            .order_by(PilotReport.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    def _render_markdown(self, data: dict[str, Any]) -> str:
        """将结构化报告数据渲染为 Markdown。"""
        lines: list[str] = []
        period = data.get("period", {})
        lines.append(f"# Pilot 周报 ({period.get('start_date', '')} ~ {period.get('end_date', '')})")
        lines.append("")

        # 使用概要
        usage = data.get("usage_summary", {})
        lines.append("## 使用概要")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总会话数 | {usage.get('total_conversations', 0)} |")
        lines.append(f"| 独立用户 | {usage.get('unique_users', 0)} |")
        lines.append(f"| 日均活跃 (DAU) | {usage.get('avg_dau', 0)} |")
        lines.append(f"| 人均问题数 | {usage.get('questions_per_user', 0)} |")
        lines.append(f"| 重复使用用户 | {usage.get('repeat_users', 0)} |")
        lines.append(f"| 重复使用率 | {usage.get('repeat_usage_rate', 0):.1%} |")
        lines.append("")

        # 任务分布
        dist = data.get("distribution", {})
        task_dist = dist.get("task_distribution", {})
        if task_dist:
            lines.append("## 任务类型分布")
            lines.append("")
            lines.append("| 任务类型 | 数量 |")
            lines.append("|----------|------|")
            for task, count in sorted(task_dist.items(), key=lambda x: -x[1]):
                lines.append(f"| {task} | {count} |")
            lines.append("")

        # 阶段分布
        stage_dist = dist.get("stage_distribution", {})
        if stage_dist:
            lines.append("## 销售阶段分布")
            lines.append("")
            lines.append("| 阶段 | 数量 |")
            lines.append("|------|------|")
            for stage, count in sorted(stage_dist.items(), key=lambda x: -x[1]):
                lines.append(f"| {stage} | {count} |")
            lines.append("")

        # 质量指标
        quality = data.get("quality", {})
        lines.append("## 质量指标")
        lines.append("")
        lines.append(f"- 正反馈比: {quality.get('feedback_positive_ratio', 0):.1%}")
        lines.append(f"- RAG 使用率: {quality.get('rag_usage_rate', 0):.1%}")
        lines.append(f"- 风险拦截数: {quality.get('risk_interception_count', 0)}")
        lines.append("")

        # 性能
        perf = data.get("performance", {})
        lines.append("## 性能指标")
        lines.append("")
        lines.append(f"- 延迟 P50: {perf.get('latency_p50_ms', 0)} ms")
        lines.append(f"- 延迟 P95: {perf.get('latency_p95_ms', 0)} ms")
        lines.append(f"- 错误率: {perf.get('error_rate', 0):.1%}")
        lines.append("")

        # 反馈分类
        fb_cats = data.get("feedback_categories", {})
        if fb_cats:
            lines.append("## 反馈根因分布")
            lines.append("")
            lines.append("| 根因 | 数量 |")
            lines.append("|------|------|")
            for cat, count in sorted(fb_cats.items(), key=lambda x: -x[1]):
                lines.append(f"| {cat} | {count} |")
            lines.append("")

        # 知识缺口
        gaps = data.get("knowledge_gaps", {})
        lines.append("## 知识缺口")
        lines.append("")
        lines.append(f"- 待补充: {gaps.get('open', 0) + gaps.get('document_needed', 0)}")
        lines.append(f"- 已上传待验证: {gaps.get('uploaded', 0)}")
        lines.append(f"- 已验证: {gaps.get('verified', 0)}")
        lines.append(f"- 已忽略: {gaps.get('ignored', 0)}")
        lines.append("")

        # 告警
        lines.append("## 活跃告警")
        lines.append("")
        lines.append(f"当前活跃告警数: {data.get('active_alerts_count', 0)}")
        lines.append("")

        lines.append("---")
        lines.append("*本报告由系统自动生成，区分观察事实与建议推论。*")

        return "\n".join(lines)
