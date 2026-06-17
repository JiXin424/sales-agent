"""运维告警服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.alert import Alert, AlertRule
from sales_agent.models.base import generate_id, utcnow

logger = logging.getLogger(__name__)

# 合法的告警指标
VALID_METRICS = {
    "model_failures",
    "dingtalk_failures",
    "ingestion_failures",
    "slow_p95",
    "high_error_rate",
    "high_negative_feedback",
    "retrieval_misses",
}

# 合法的条件操作符
VALID_CONDITIONS = {"gt", "gte", "lt", "lte"}

# 合法的严重级别
VALID_SEVERITIES = {"info", "warning", "critical"}


class AlertService:
    """告警规则管理和告警评估。

    用法::

        svc = AlertService(db)
        rule = await svc.create_rule(tenant_id, name="高错误率", metric="high_error_rate", threshold=0.1)
        alerts = await svc.evaluate_rules(tenant_id)
        alert = await svc.acknowledge_alert(tenant_id, alert_id)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- 规则 CRUD ---

    async def create_rule(
        self,
        tenant_id: str,
        name: str,
        metric: str,
        threshold: float,
        condition: str = "gt",
        window_minutes: int = 60,
        severity: str = "warning",
    ) -> AlertRule:
        """创建告警规则。"""
        if metric not in VALID_METRICS:
            raise ValueError(f"Invalid metric: {metric!r}. Must be one of {VALID_METRICS}")
        if condition not in VALID_CONDITIONS:
            raise ValueError(f"Invalid condition: {condition!r}")
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {severity!r}")

        rule = AlertRule(
            id=generate_id(),
            tenant_id=tenant_id,
            name=name,
            metric=metric,
            condition=condition,
            threshold=threshold,
            window_minutes=window_minutes,
            severity=severity,
            enabled="true",
        )
        self.db.add(rule)
        await self.db.flush()
        return rule

    async def update_rule(
        self,
        tenant_id: str,
        rule_id: str,
        **kwargs: Any,
    ) -> AlertRule:
        """更新告警规则。"""
        rule = await self.get_rule(tenant_id, rule_id)
        if rule is None:
            raise ValueError(f"Alert rule not found: {rule_id!r}")

        for key, value in kwargs.items():
            if hasattr(rule, key) and value is not None:
                setattr(rule, key, value)

        await self.db.flush()
        return rule

    async def get_rule(self, tenant_id: str, rule_id: str) -> AlertRule | None:
        """获取单条告警规则。"""
        stmt = select(AlertRule).where(
            AlertRule.id == rule_id,
            AlertRule.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_rules(
        self,
        tenant_id: str,
        enabled_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AlertRule], int]:
        """列出告警规则。"""
        conditions = [AlertRule.tenant_id == tenant_id]
        if enabled_only:
            conditions.append(AlertRule.enabled == "true")

        total = (await self.db.execute(
            select(func.count()).select_from(AlertRule).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(AlertRule)
            .where(*conditions)
            .order_by(AlertRule.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    # --- 告警评估 ---

    async def evaluate_rules(self, tenant_id: str) -> list[Alert]:
        """评估所有启用的规则，触发告警。

        返回新创建或更新的告警列表。
        """
        rules_stmt = select(AlertRule).where(
            AlertRule.tenant_id == tenant_id,
            AlertRule.enabled == "true",
        )
        rules = list((await self.db.execute(rules_stmt)).scalars().all())

        new_alerts: list[Alert] = []
        for rule in rules:
            observed = await self._compute_metric(tenant_id, rule.metric, rule.window_minutes)
            breached = self._check_threshold(observed, rule.condition, rule.threshold)

            if breached:
                alert = await self._upsert_alert(
                    tenant_id=tenant_id,
                    rule=rule,
                    observed_value=observed,
                )
                new_alerts.append(alert)

        await self.db.flush()
        return new_alerts

    # --- 告警管理 ---

    async def list_alerts(
        self,
        tenant_id: str,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Alert], int]:
        """列出告警记录。"""
        conditions = [Alert.tenant_id == tenant_id]
        if status:
            conditions.append(Alert.status == status)
        if severity:
            conditions.append(Alert.severity == severity)

        total = (await self.db.execute(
            select(func.count()).select_from(Alert).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(Alert)
            .where(*conditions)
            .order_by(Alert.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def acknowledge_alert(self, tenant_id: str, alert_id: str) -> Alert:
        """确认告警。"""
        alert = await self._get_alert(tenant_id, alert_id)
        if alert is None:
            raise ValueError(f"Alert not found: {alert_id!r}")
        alert.status = "acknowledged"
        await self.db.flush()
        return alert

    async def resolve_alert(self, tenant_id: str, alert_id: str) -> Alert:
        """解决告警。"""
        alert = await self._get_alert(tenant_id, alert_id)
        if alert is None:
            raise ValueError(f"Alert not found: {alert_id!r}")
        alert.status = "resolved"
        await self.db.flush()
        return alert

    # --- 默认规则种子 ---

    async def seed_default_rules(self, tenant_id: str) -> list[AlertRule]:
        """为租户创建默认告警规则。"""
        defaults = [
            {"name": "模型调用失败过多", "metric": "model_failures", "condition": "gt", "threshold": 5, "window_minutes": 60, "severity": "critical"},
            {"name": "P95 延迟过高", "metric": "slow_p95", "condition": "gt", "threshold": 10000, "window_minutes": 60, "severity": "warning"},
            {"name": "错误率过高", "metric": "high_error_rate", "condition": "gt", "threshold": 0.1, "window_minutes": 60, "severity": "warning"},
            {"name": "负反馈率过高", "metric": "high_negative_feedback", "condition": "gt", "threshold": 0.3, "window_minutes": 60, "severity": "warning"},
            {"name": "检索缺失过多", "metric": "retrieval_misses", "condition": "gt", "threshold": 10, "window_minutes": 60, "severity": "warning"},
        ]

        rules = []
        for d in defaults:
            rule = await self.create_rule(tenant_id=tenant_id, **d)
            rules.append(rule)
        return rules

    # --- 内部方法 ---

    async def _get_alert(self, tenant_id: str, alert_id: str) -> Alert | None:
        stmt = select(Alert).where(
            Alert.id == alert_id,
            Alert.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _compute_metric(
        self, tenant_id: str, metric: str, window_minutes: int
    ) -> float:
        """计算指定指标的当前值。"""
        from sales_agent.models.agent_run import AgentRun
        from sales_agent.models.conversation import Conversation
        from sales_agent.models.feedback import Feedback

        now_str = utcnow()

        if metric == "model_failures":
            stmt = select(func.count()).select_from(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.status == "failed",
            )
            return float((await self.db.execute(stmt)).scalar() or 0)

        elif metric == "high_error_rate":
            total = (await self.db.execute(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.tenant_id == tenant_id,
                )
            )).scalar() or 1
            failed = (await self.db.execute(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.status == "failed",
                )
            )).scalar() or 0
            return round(failed / total, 3) if total else 0

        elif metric == "high_negative_feedback":
            total = (await self.db.execute(
                select(func.count()).select_from(Feedback).where(
                    Feedback.tenant_id == tenant_id,
                )
            )).scalar() or 1
            down = (await self.db.execute(
                select(func.count()).select_from(Feedback).where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.rating == "down",
                )
            )).scalar() or 0
            return round(down / total, 3) if total else 0

        elif metric == "slow_p95":
            stmt = select(AgentRun.total_latency_ms).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.total_latency_ms.isnot(None),
            ).order_by(AgentRun.total_latency_ms)
            result = await self.db.execute(stmt)
            latencies = [r[0] for r in result.all()]
            if latencies:
                n = len(latencies)
                p95_idx = min(int(n * 0.95), n - 1)
                return float(latencies[p95_idx])
            return 0.0

        elif metric == "retrieval_misses":
            stmt = select(func.count()).select_from(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.task_type == "knowledge_qa",
            )
            total_kq = (await self.db.execute(stmt)).scalar() or 0

            miss_stmt = select(func.count()).select_from(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.task_type == "knowledge_qa",
                (Conversation.sources_json == "[]") | (Conversation.sources_json == "null") | (Conversation.sources_json.is_(None)),
            )
            misses = (await self.db.execute(miss_stmt)).scalar() or 0
            return float(misses)

        elif metric == "dingtalk_failures":
            # 查询 DingTalk 相关表（如果存在）
            return 0.0

        elif metric == "ingestion_failures":
            from sales_agent.models.ingestion import IngestionJob
            stmt = select(func.count()).select_from(IngestionJob).where(
                IngestionJob.tenant_id == tenant_id,
                IngestionJob.status == "failed",
            )
            return float((await self.db.execute(stmt)).scalar() or 0)

        return 0.0

    def _check_threshold(
        self, observed: float, condition: str, threshold: float
    ) -> bool:
        """检查是否触发阈值。"""
        ops = {
            "gt": lambda o, t: o > t,
            "gte": lambda o, t: o >= t,
            "lt": lambda o, t: o < t,
            "lte": lambda o, t: o <= t,
        }
        return ops.get(condition, lambda o, t: False)(observed, threshold)

    async def _upsert_alert(
        self,
        tenant_id: str,
        rule: AlertRule,
        observed_value: float,
    ) -> Alert:
        """创建或更新告警记录（按 rule_id + active 状态去重）。"""
        # 查找该规则当前 active 的告警
        stmt = select(Alert).where(
            Alert.tenant_id == tenant_id,
            Alert.alert_rule_id == rule.id,
            Alert.status == "active",
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()

        now = utcnow()
        if existing:
            existing.observed_value = observed_value
            existing.last_seen_at = now
            return existing

        alert = Alert(
            id=generate_id(),
            tenant_id=tenant_id,
            alert_rule_id=rule.id,
            severity=rule.severity,
            metric=rule.metric,
            threshold_value=rule.threshold,
            observed_value=observed_value,
            status="active",
            first_seen_at=now,
            last_seen_at=now,
        )
        self.db.add(alert)
        return alert
