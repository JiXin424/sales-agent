"""Agent 激活就绪检查。

draft Agent 激活前必须通过必检项：model config、prompt set、knowledge scope（如必填）、
channel（如启用）、eval（已通过或豁免）。豁免记录在 Agent.feature_flags_json 的
activation_waivers 中。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent import Agent
from sales_agent.services.agent_service import AgentService, _agent_to_dict

logger = logging.getLogger(__name__)


class ReadinessService:
    """计算 Agent 的激活就绪状态。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def evaluate(self, agent: Agent) -> dict[str, Any]:
        svc = AgentService(self.db)
        flags = _parse_flags(agent)
        waivers = set(flags.get("activation_waivers", {}).keys())

        checks: list[dict[str, Any]] = []

        # 1. 模型配置
        model_ok = bool(agent.model_config_ref)
        checks.append({
            "check": "model_config",
            "label": "模型配置",
            "passed": model_ok,
            "required": True,
            "reason": "" if model_ok else "缺少模型配置引用",
        })

        # 2. Prompt set
        checks.append({
            "check": "prompt_set",
            "label": "Prompt 集合",
            "passed": prompt_ok,
            "required": True,
            "reason": "" if prompt_ok else "未绑定活跃 prompt 集合",
        })

        # 3. 知识作用域（仅当 feature flag require_knowledge 时为必填）
        require_knowledge = bool(flags.get("require_knowledge", False))
        scope_ok = bool(agent.knowledge_scope_id)
        checks.append({
            "check": "knowledge_scope",
            "label": "知识作用域",
            "passed": scope_ok,
            "required": require_knowledge,
            "reason": "" if scope_ok else "未配置知识作用域",
        })

        # 4. 风险策略（推荐但非必填，缺失会回退到 tenant config）
        risk_ok = bool(agent.risk_policy_id)
        checks.append({
            "check": "risk_policy",
            "label": "风险策略",
            "passed": risk_ok,
            "required": False,
            "reason": "" if risk_ok else "未配置独立风险策略（将回退到租户配置）",
        })

        # 5. 渠道（仅当启用渠道时为必填）
        channels_enabled = bool(flags.get("channels_enabled", False))
        channel_ok = await self._channel_ok(agent)
        checks.append({
            "check": "channel_config",
            "label": "渠道配置",
            "passed": channel_ok,
            "required": channels_enabled,
            "reason": "" if channel_ok else "渠道未验证",
        })

        # 6. Eval（如 feature flag require_eval，需通过或豁免）
        require_eval = bool(flags.get("require_eval", False))
        eval_passed = bool(flags.get("eval_passed", False))
        eval_waived = "eval" in waivers
        eval_ok = eval_passed or eval_waived or (not require_eval)
        checks.append({
            "check": "eval",
            "label": "Eval 回归",
            "passed": eval_ok,
            "required": require_eval,
            "reason": "" if eval_ok else (
                "已豁免" if eval_waived else "Eval 未通过"
            ),
        })

        # 计算阻塞项（必填未通过且未豁免）
        blockers: list[str] = []
        for c in checks:
            if c["required"] and not c["passed"] and c["check"] not in waivers:
                blockers.append(c["reason"] or c["label"])

        ready = len(blockers) == 0
        return {
            "agent_id": agent.id,
            "ready": ready,
            "checks": checks,
            "blockers": blockers,
            "waivers": sorted(waivers),
        }

    async def _channel_ok(self, agent: Agent) -> bool:
        from sales_agent.models.agent_channel_config import AgentChannelConfig
        from sqlalchemy import select
        row = (
            await self.db.execute(
                select(AgentChannelConfig).where(
                    AgentChannelConfig.agent_id == agent.id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        return row.status in ("configured", "verified")


def _parse_flags(agent: Agent) -> dict[str, Any]:
    try:
        return json.loads(agent.feature_flags_json) if agent.feature_flags_json else {}
    except json.JSONDecodeError:
        return {}


async def can_activate(db: AsyncSession, agent: Agent) -> tuple[bool, dict[str, Any]]:
    """返回 (可否激活, 就绪报告)。archived/paused 不允许直接 active。"""
    report = await ReadinessService(db).evaluate(agent)
    return report["ready"], report
