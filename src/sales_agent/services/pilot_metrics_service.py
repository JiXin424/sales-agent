"""Pilot 成功指标计算服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent_run import AgentRun
from sales_agent.models.conversation import Conversation, RetrievalLog
from sales_agent.models.feedback import Feedback

logger = logging.getLogger(__name__)


class PilotMetricsService:
    """Pilot 成功指标计算。

    用法::

        svc = PilotMetricsService(db)
        metrics = await svc.get_metrics(tenant_id, "2026-01-01", "2026-06-11")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_metrics(
        self, tenant_id: str, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """计算指定时间范围内的 Pilot 成功指标。"""
        date_filter = Conversation.tenant_id == tenant_id

        if start_date:
            date_filter = date_filter & (Conversation.created_at >= start_date)
        if end_date:
            date_filter = date_filter & (Conversation.created_at <= end_date + "T23:59:59")

        return {
            "usage": await self._usage_metrics(tenant_id, date_filter),
            "distribution": await self._distribution_metrics(tenant_id, date_filter),
            "quality": await self._quality_metrics(tenant_id, start_date, end_date),
            "performance": await self._performance_metrics(tenant_id, start_date, end_date),
            "period": {"start_date": start_date, "end_date": end_date},
        }

    async def _usage_metrics(
        self, tenant_id: str, date_filter: Any
    ) -> dict[str, Any]:
        """使用量指标：DAU/WAU、活跃用户、人均问题数、复用率。"""
        # 总会话数和独立用户
        conv_stmt = select(
            func.count().label("total_conversations"),
            func.count(func.distinct(Conversation.user_id)).label("unique_users"),
        ).where(date_filter)
        conv_result = await self.db.execute(conv_stmt)
        row = conv_result.one()
        total_conversations = row.total_conversations or 0
        unique_users = row.unique_users or 0

        # DAU: 按天分组计算每天独立用户数，取平均
        daily_users_stmt = select(
            func.count(func.distinct(Conversation.user_id)).label("daily_users"),
        ).where(date_filter).group_by(
            func.date(Conversation.created_at)
        )
        daily_result = await self.db.execute(daily_users_stmt)
        daily_counts = [r[0] for r in daily_result.all()]
        avg_dau = round(sum(daily_counts) / len(daily_counts), 1) if daily_counts else 0

        # 重复使用用户: >=3 次对话的用户
        repeat_stmt = select(
            func.count().label("cnt"),
        ).where(date_filter).group_by(
            Conversation.user_id,
        )
        repeat_result = await self.db.execute(repeat_stmt)
        repeat_users = sum(1 for r in repeat_result.all() if r[0] >= 3)

        questions_per_user = round(total_conversations / unique_users, 1) if unique_users else 0
        repeat_rate = round(repeat_users / unique_users, 3) if unique_users else 0

        return {
            "total_conversations": total_conversations,
            "unique_users": unique_users,
            "avg_dau": avg_dau,
            "questions_per_user": questions_per_user,
            "repeat_users": repeat_users,
            "repeat_usage_rate": repeat_rate,
        }

    async def _distribution_metrics(
        self, tenant_id: str, date_filter: Any
    ) -> dict[str, Any]:
        """分布指标：任务类型分布、阶段分布。"""
        # 任务类型分布
        task_stmt = (
            select(Conversation.task_type, func.count())
            .where(date_filter, Conversation.task_type.isnot(None))
            .group_by(Conversation.task_type)
            .order_by(func.count().desc())
        )
        task_result = await self.db.execute(task_stmt)
        task_distribution = {r[0]: r[1] for r in task_result.all()}

        # 阶段分布
        stage_stmt = (
            select(Conversation.stage, func.count())
            .where(date_filter, Conversation.stage.isnot(None))
            .group_by(Conversation.stage)
            .order_by(func.count().desc())
        )
        stage_result = await self.db.execute(stage_stmt)
        stage_distribution = {r[0]: r[1] for r in stage_result.all()}

        return {
            "task_distribution": task_distribution,
            "stage_distribution": stage_distribution,
        }

    async def _quality_metrics(
        self, tenant_id: str, start_date: str | None, end_date: str | None
    ) -> dict[str, Any]:
        """质量指标：反馈比、RAG 使用率、风险拦截数。"""
        fb_conditions = [Feedback.tenant_id == tenant_id]
        if start_date:
            fb_conditions.append(Feedback.created_at >= start_date)
        if end_date:
            fb_conditions.append(Feedback.created_at <= end_date + "T23:59:59")

        fb_stmt = select(Feedback.rating, func.count()).where(*fb_conditions).group_by(Feedback.rating)
        fb_result = await self.db.execute(fb_stmt)
        fb_counts = dict(fb_result.all())
        up_count = fb_counts.get("up", 0)
        down_count = fb_counts.get("down", 0)
        total_fb = up_count + down_count
        positive_ratio = round(up_count / total_fb, 3) if total_fb else 0

        # RAG 使用率: 有 sources 的会话占比
        conv_conditions = [Conversation.tenant_id == tenant_id]
        if start_date:
            conv_conditions.append(Conversation.created_at >= start_date)
        if end_date:
            conv_conditions.append(Conversation.created_at <= end_date + "T23:59:59")

        total_conv_stmt = select(func.count()).select_from(Conversation).where(*conv_conditions)
        total_conv = (await self.db.execute(total_conv_stmt)).scalar() or 0

        rag_conv_stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(*conv_conditions, Conversation.sources_json != "[]", Conversation.sources_json != "null")
        )
        rag_conv = (await self.db.execute(rag_conv_stmt)).scalar() or 0
        rag_usage_rate = round(rag_conv / total_conv, 3) if total_conv else 0

        # 风险拦截数: risk_json 包含高等级风险的会话
        risk_conv_stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(*conv_conditions, Conversation.risk_json.isnot(None), Conversation.risk_json != "")
        )
        risk_count = (await self.db.execute(risk_conv_stmt)).scalar() or 0

        return {
            "feedback_up": up_count,
            "feedback_down": down_count,
            "feedback_positive_ratio": positive_ratio,
            "rag_usage_rate": rag_usage_rate,
            "risk_interception_count": risk_count,
        }

    async def _performance_metrics(
        self, tenant_id: str, start_date: str | None, end_date: str | None
    ) -> dict[str, Any]:
        """性能指标：延迟 P50/P95、错误率。"""
        run_conditions = [AgentRun.tenant_id == tenant_id]
        if start_date:
            run_conditions.append(AgentRun.created_at >= start_date)
        if end_date:
            run_conditions.append(AgentRun.created_at <= end_date + "T23:59:59")

        # 延迟统计
        latency_stmt = (
            select(AgentRun.total_latency_ms)
            .where(*run_conditions, AgentRun.total_latency_ms.isnot(None))
            .order_by(AgentRun.total_latency_ms)
        )
        latency_result = await self.db.execute(latency_stmt)
        latencies = [r[0] for r in latency_result.all()]

        if latencies:
            latencies_sorted = sorted(latencies)
            n = len(latencies_sorted)
            p50_idx = min(int(n * 0.5), n - 1)
            p95_idx = min(int(n * 0.95), n - 1)
            latency_p50 = latencies_sorted[p50_idx]
            latency_p95 = latencies_sorted[p95_idx]
        else:
            latency_p50 = 0
            latency_p95 = 0

        # 错误率
        total_runs_stmt = select(func.count()).select_from(AgentRun).where(*run_conditions)
        total_runs = (await self.db.execute(total_runs_stmt)).scalar() or 0

        failed_runs_stmt = (
            select(func.count())
            .select_from(AgentRun)
            .where(*run_conditions, AgentRun.status == "failed")
        )
        failed_runs = (await self.db.execute(failed_runs_stmt)).scalar() or 0
        error_rate = round(failed_runs / total_runs, 3) if total_runs else 0

        return {
            "latency_p50_ms": latency_p50,
            "latency_p95_ms": latency_p95,
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "error_rate": error_rate,
        }
