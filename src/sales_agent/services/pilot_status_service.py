"""Pilot 退出决策框架服务。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.alert import Alert
from sales_agent.models.conversation import Conversation
from sales_agent.models.feedback import Feedback
from sales_agent.models.knowledge_gap import KnowledgeGap
from sales_agent.models.review_item import ReviewItem

logger = logging.getLogger(__name__)


class PilotStatusService:
    """Pilot 退出决策评估。

    用法::

        svc = PilotStatusService(db)
        status = await svc.get_pilot_status(tenant_id)
        print(status["classification"])  # expand / continue_pilot / needs_remediation / stop
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_pilot_status(self, tenant_id: str) -> dict[str, Any]:
        """计算 Pilot 状态分类。

        基于激活率、重复使用率、反馈比、审查状态、知识缺口、告警等综合评估。
        """
        # 收集各维度数据
        metrics = await self._collect_metrics(tenant_id)

        # 决策逻辑
        classification, reasons, next_actions = self._classify(metrics)

        return {
            "tenant_id": tenant_id,
            "classification": classification,
            "reasons": reasons,
            "next_actions": next_actions,
            "metrics": metrics,
        }

    async def _collect_metrics(self, tenant_id: str) -> dict[str, Any]:
        """收集决策所需的各项指标。"""
        # 总会话数和独立用户
        conv_stmt = select(
            func.count().label("total"),
            func.count(func.distinct(Conversation.user_id)).label("users"),
        ).where(Conversation.tenant_id == tenant_id)
        conv_row = (await self.db.execute(conv_stmt)).one()
        total_conversations = conv_row.total or 0
        unique_users = conv_row.users or 0

        # 重复使用率: >=3 次对话的用户占比
        repeat_stmt = select(Conversation.user_id).where(
            Conversation.tenant_id == tenant_id,
        ).group_by(Conversation.user_id)
        repeat_result = await self.db.execute(repeat_stmt)
        user_conv_counts = {}
        for row in repeat_result.all():
            uid = row[0]
            user_conv_counts[uid] = user_conv_counts.get(uid, 0)

        # 单独计算每用户会话数
        per_user_stmt = (
            select(Conversation.user_id, func.count())
            .where(Conversation.tenant_id == tenant_id)
            .group_by(Conversation.user_id)
        )
        per_user_result = await self.db.execute(per_user_stmt)
        user_counts = dict(per_user_result.all())
        repeat_users = sum(1 for c in user_counts.values() if c >= 3)
        repeat_usage_rate = round(repeat_users / unique_users, 3) if unique_users else 0

        # 反馈正比率
        fb_stmt = select(Feedback.rating, func.count()).where(
            Feedback.tenant_id == tenant_id,
        ).group_by(Feedback.rating)
        fb_counts = dict((await self.db.execute(fb_stmt)).all())
        total_fb = sum(fb_counts.values())
        positive_ratio = round(fb_counts.get("up", 0) / total_fb, 3) if total_fb else 0

        # 未解决审查条目
        open_reviews = (await self.db.execute(
            select(func.count()).select_from(ReviewItem).where(
                ReviewItem.tenant_id == tenant_id,
                ReviewItem.status.in_(["open", "in_progress"]),
            )
        )).scalar() or 0

        # 未解决知识缺口
        unresolved_gaps = (await self.db.execute(
            select(func.count()).select_from(KnowledgeGap).where(
                KnowledgeGap.tenant_id == tenant_id,
                KnowledgeGap.status.in_(["open", "document_needed"]),
            )
        )).scalar() or 0

        # 活跃告警
        active_alerts = (await self.db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.tenant_id == tenant_id,
                Alert.status == "active",
                Alert.severity == "critical",
            )
        )).scalar() or 0

        # 近期错误率（7天）
        from datetime import datetime, timezone, timedelta
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        from sales_agent.models.agent_run import AgentRun
        total_recent = (await self.db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.created_at >= seven_days_ago,
            )
        )).scalar() or 0

        failed_recent = (await self.db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.created_at >= seven_days_ago,
                AgentRun.status == "failed",
            )
        )).scalar() or 0

        recent_error_rate = round(failed_recent / total_recent, 3) if total_recent else 0

        return {
            "total_conversations": total_conversations,
            "unique_users": unique_users,
            "repeat_users": repeat_users,
            "repeat_usage_rate": repeat_usage_rate,
            "feedback_positive_ratio": positive_ratio,
            "total_feedback": total_fb,
            "open_reviews": open_reviews,
            "unresolved_gaps": unresolved_gaps,
            "active_critical_alerts": active_alerts,
            "recent_error_rate": recent_error_rate,
        }

    def _classify(
        self, m: dict[str, Any]
    ) -> tuple[str, list[str], list[str]]:
        """基于指标进行分类决策。

        返回 (classification, reasons, next_actions)。
        """
        reasons: list[str] = []
        next_actions: list[str] = []

        # 规则 1: stop — 激活极低 + 反馈极差 + 错误率高
        if (
            m["unique_users"] < 3
            and m["feedback_positive_ratio"] < 0.3
            and m["recent_error_rate"] > 0.2
        ):
            reasons.append("激活用户极少且反馈差")
            reasons.append(f"近期错误率 {m['recent_error_rate']:.1%} 过高")
            next_actions.append("暂停 pilot，排查系统稳定性问题")
            next_actions.append("与客户沟通产品匹配度")
            return "stop", reasons, next_actions

        # 规则 2: needs_remediation — 反馈差 或 知识缺口多 或 有严重告警
        if m["feedback_positive_ratio"] < 0.5 and m["total_feedback"] >= 5:
            reasons.append(f"正反馈比 {m['feedback_positive_ratio']:.1%} 低于 50%")
            next_actions.append("重点分析负反馈根因并改进")
        if m["unresolved_gaps"] > 5:
            reasons.append(f"有 {m['unresolved_gaps']} 个未解决知识缺口")
            next_actions.append("优先补充缺失知识文档")
        if m["active_critical_alerts"] > 0:
            reasons.append(f"有 {m['active_critical_alerts']} 个严重告警未解决")
            next_actions.append("立即排查并解决严重告警")

        if reasons:
            next_actions.append("问题修复后重新评估")
            return "needs_remediation", reasons, next_actions

        # 规则 3: expand — 激活好 + 重复好 + 反馈好 + 审查少 + 无严重告警
        if (
            m["unique_users"] >= 5
            and m["repeat_usage_rate"] >= 0.4
            and m["feedback_positive_ratio"] >= 0.7
            and m["open_reviews"] < 5
            and m["active_critical_alerts"] == 0
            and m["unresolved_gaps"] <= 2
        ):
            reasons.append("用户活跃度高")
            reasons.append(f"重复使用率 {m['repeat_usage_rate']:.1%} 达标")
            reasons.append(f"正反馈比 {m['feedback_positive_ratio']:.1%} 优良")
            next_actions.append("准备扩大部署范围")
            next_actions.append("与客户讨论正式采购方案")
            return "expand", reasons, next_actions

        # 默认: continue_pilot
        reasons.append("各项指标尚可，但未达到扩展标准")
        if m["unique_users"] < 5:
            reasons.append("活跃用户数量偏少")
            next_actions.append("推动更多销售用户试用")
        if m["repeat_usage_rate"] < 0.4:
            reasons.append("重复使用率偏低")
            next_actions.append("分析低复用原因，优化产品粘性")
        if m["feedback_positive_ratio"] < 0.7:
            next_actions.append("持续改进答案质量")
        if m["open_reviews"] >= 5:
            next_actions.append("尽快处理积压的审查条目")
        next_actions.append("继续收集数据，下周重新评估")

        return "continue_pilot", reasons, next_actions
