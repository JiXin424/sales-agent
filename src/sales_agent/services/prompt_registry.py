"""Prompt 注册表服务：运行时 prompt 解析、版本管理和生命周期。"""

from __future__ import annotations

import json
import logging
import string
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.prompt import PromptVersion
from sales_agent.services.task_router import ALL_TASK_TYPES

logger = logging.getLogger(__name__)

# Python 常量默认 prompt（兼容回退）
from sales_agent.prompts import (
    emotional_support,
    knowledge_qa,
    script_generation,
    objection_handling,
    conversation_review,
    general_coaching,
    visit_preparation,
    follow_up_planning,
    customer_context_summary,
    deal_advancement,
    conversation_scoring,
    post_visit_review,
)

_DEFAULT_PROMPTS = {
    "emotional_support": emotional_support.EMOTIONAL_SUPPORT_PROMPT,
    "knowledge_qa": knowledge_qa.KNOWLEDGE_QA_PROMPT,
    "script_generation": script_generation.SCRIPT_GENERATION_PROMPT,
    "objection_handling": objection_handling.OBJECTION_HANDLING_PROMPT,
    "conversation_review": conversation_review.CONVERSATION_REVIEW_PROMPT,
    "general_sales_coaching": general_coaching.GENERAL_COACHING_PROMPT,
    "visit_preparation": visit_preparation.VISIT_PREPARATION_PROMPT,
    "follow_up_planning": follow_up_planning.FOLLOW_UP_PLANNING_PROMPT,
    "customer_context_summary": customer_context_summary.CUSTOMER_CONTEXT_SUMMARY_PROMPT,
    "deal_advancement": deal_advancement.DEAL_ADVANCEMENT_PROMPT,
    "conversation_scoring": conversation_scoring.CONVERSATION_SCORING_PROMPT,
    "post_visit_review": post_visit_review.POST_VISIT_REVIEW_PROMPT,
}

# 模板中必须包含的占位符
_REQUIRED_PLACEHOLDERS = {"message"}


def _validate_task_type(task_type: str) -> None:
    """校验 task_type 是否合法。"""
    if task_type not in ALL_TASK_TYPES:
        raise ValueError(
            f"Invalid task_type: {task_type!r}. "
            f"Must be one of: {', '.join(ALL_TASK_TYPES)}"
        )


def _validate_template(template_text: str) -> None:
    """校验模板必须包含 {message} 占位符，防止缺失变量静默失败。"""
    formatter = string.Formatter()
    try:
        field_names = {
            fname for _, fname, _, _ in formatter.parse(template_text) if fname
        }
    except (ValueError, IndexError):
        raise ValueError("Prompt template contains invalid format placeholders.")

    missing = _REQUIRED_PLACEHOLDERS - field_names
    if missing:
        raise ValueError(
            f"Prompt template is missing required placeholder(s): {', '.join(missing)}. "
            f"Every template must contain {{{', '.join(missing)}}}."
        )


class PromptRegistry:
    """运行时 Prompt 解析与版本管理。

    用法::

        registry = PromptRegistry(db)
        template = await registry.resolve(tenant_id, "knowledge_qa")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def resolve(self, tenant_id: str, task_type: str) -> str:
        """解析租户+任务类型的活跃 prompt。

        优先返回租户级 active 版本，否则回退到 Python 常量默认值。
        """
        stmt = select(PromptVersion).where(
            PromptVersion.tenant_id == tenant_id,
            PromptVersion.task_type == task_type,
            PromptVersion.status == "active",
        ).limit(1)
        result = await self.db.execute(stmt)
        version = result.scalar_one_or_none()

        if version is not None:
            logger.debug(
                "Resolved tenant prompt: tenant=%s task=%s version_id=%s",
                tenant_id, task_type, version.id,
            )
            return version.template_text

        # 回退到默认 prompt
        default = _DEFAULT_PROMPTS.get(task_type)
        if default is not None:
            logger.debug(
                "Using default prompt: tenant=%s task=%s", tenant_id, task_type,
            )
            return default

        # 不应该到这里，因为 task_type 已校验
        raise ValueError(f"No prompt found for task_type: {task_type!r}")

    async def resolve_for_agent(
        self, agent_id: str | None, tenant_id: str, task_type: str
    ) -> str:
        """解析 Agent 作用域的 prompt。

        解析顺序：
          1. Agent prompt_set 中该 task_type 映射的版本（独立副本，状态不限）；
          2. tenant 级 active 版本；
          3. Python 常量默认值。
        agent_id 为 None 或无映射时回退到 tenant/默认（向后兼容）。
        """
        if agent_id:
            mapped = await self._resolve_agent_prompt_version(agent_id, task_type)
            if mapped is not None:
                logger.debug(
                    "Resolved agent prompt: agent=%s task=%s version_id=%s",
                    agent_id, task_type, mapped.id,
                )
                return mapped.template_text
        return await self.resolve(tenant_id, task_type)

    async def _resolve_agent_prompt_version(self, agent_id: str, task_type: str):
        """从 Agent prompt_set 映射里取出 task_type → PromptVersion。"""
        from sales_agent.models.agent import Agent
        from sales_agent.models.agent_prompt_set import AgentPromptSet
        # 先取 Agent 当前绑定的 prompt_set_id（避免一个 Agent 多个 set 时取错）
        agent = (
            await self.db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None or not agent.prompt_set_id:
            return None
        ps = (
            await self.db.execute(
                select(AgentPromptSet).where(AgentPromptSet.id == agent.prompt_set_id)
            )
        ).scalar_one_or_none()
        if ps is None:
            return None
        try:
            mapping = json.loads(ps.task_prompt_versions_json or "{}")
        except (ValueError, json.JSONDecodeError):
            return None
        version_id = mapping.get(task_type)
        if not version_id:
            return None
        pv = (
            await self.db.execute(
                select(PromptVersion).where(PromptVersion.id == version_id)
            )
        ).scalar_one_or_none()
        return pv

    async def create_version(
        self,
        tenant_id: str,
        task_type: str,
        template_text: str,
        description: str = "",
        version: str = "",
    ) -> PromptVersion:
        """创建一个 draft 版本的 prompt。"""
        _validate_task_type(task_type)
        _validate_template(template_text)

        pv = PromptVersion(
            tenant_id=tenant_id,
            task_type=task_type,
            version=version,
            status="draft",
            template_text=template_text,
            description=description,
        )
        self.db.add(pv)
        await self.db.flush()
        return pv

    async def activate_version(self, tenant_id: str, version_id: str) -> PromptVersion:
        """激活一个 draft 版本。

        同一 tenant+task_type 的当前 active 版本会被自动归档。
        """
        pv = await self._get_version_with_tenant_check(version_id, tenant_id)
        if pv is None:
            raise ValueError(f"Prompt version not found: {version_id!r}")
        if pv.status != "draft":
            raise ValueError(
                f"Only draft versions can be activated, current status: {pv.status!r}"
            )

        # 归档同 tenant+task_type 的当前 active 版本
        stmt = select(PromptVersion).where(
            PromptVersion.tenant_id == tenant_id,
            PromptVersion.task_type == pv.task_type,
            PromptVersion.status == "active",
        )
        result = await self.db.execute(stmt)
        current_active = result.scalar_one_or_none()
        if current_active is not None:
            current_active.status = "archived"
            await self.db.flush()

        pv.status = "active"
        await self.db.flush()
        return pv

    async def archive_version(self, tenant_id: str, version_id: str) -> PromptVersion:
        """归档一个 prompt 版本。"""
        pv = await self._get_version_with_tenant_check(version_id, tenant_id)
        if pv is None:
            raise ValueError(f"Prompt version not found: {version_id!r}")
        if pv.status == "archived":
            raise ValueError("Version is already archived.")
        pv.status = "archived"
        await self.db.flush()
        return pv

    async def update_draft(
        self,
        tenant_id: str,
        version_id: str,
        template_text: str | None = None,
        description: str | None = None,
    ) -> PromptVersion:
        """更新一个 draft 版本的内容。"""
        pv = await self._get_version_with_tenant_check(version_id, tenant_id)
        if pv is None:
            raise ValueError(f"Prompt version not found: {version_id!r}")
        if pv.status != "draft":
            raise ValueError("Only draft versions can be updated.")

        if template_text is not None:
            _validate_template(template_text)
            pv.template_text = template_text
        if description is not None:
            pv.description = description

        await self.db.flush()
        return pv

    async def list_versions(
        self,
        tenant_id: str,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromptVersion], int]:
        """列出租户的 prompt 版本。返回 (versions, total)。"""
        conditions = [PromptVersion.tenant_id == tenant_id]
        if task_type is not None:
            _validate_task_type(task_type)
            conditions.append(PromptVersion.task_type == task_type)
        if status is not None:
            conditions.append(PromptVersion.status == status)

        # Count
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(PromptVersion).where(*conditions)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data
        stmt = (
            select(PromptVersion)
            .where(*conditions)
            .order_by(PromptVersion.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        versions = list(result.scalars().all())

        return versions, total

    async def get_version(
        self, tenant_id: str, version_id: str
    ) -> PromptVersion | None:
        """获取特定 prompt 版本（含租户隔离检查）。"""
        return await self._get_version_with_tenant_check(version_id, tenant_id)

    async def _get_version_with_tenant_check(
        self, version_id: str, tenant_id: str
    ) -> PromptVersion | None:
        """内部方法：按 ID 获取版本并校验租户归属。"""
        stmt = select(PromptVersion).where(
            PromptVersion.id == version_id,
            PromptVersion.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
