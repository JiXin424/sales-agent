"""Prompt 注册表服务：运行时 prompt 解析、版本管理和生命周期。

统一承载所有层 prompt（task / system / router / risk / coach）。每个 ``(category, key)``
组合在每个 tenant 下最多一个 active 版本。解析三级回退：

  1. Agent ``prompt_set`` 映射 → 具体版本（独立副本，状态不限）；
  2. tenant 级 active 版本（``status='active'``）；
  3. ``prompt_defaults.BUILTIN_PROMPTS`` 内置常量。

AgentPromptSet 的 ``task_prompt_versions_json`` 支持两种 schema（读取时自动兼容）：
  - 新（嵌套）：``{"task": {"knowledge_qa": "<id>"}, "system": {...}, "coach": {...}}``
  - 旧（扁平）：``{"knowledge_qa": "<id>"}``  —— 仅 task 类，顶层值为字符串。
"""

from __future__ import annotations

import json
import logging
import string
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.prompt import PromptVersion
from sales_agent.services.prompt_defaults import (
    get_builtin,
    required_placeholders_for,
)
from sales_agent.services.task_router import ALL_TASK_TYPES

logger = logging.getLogger(__name__)


def _build_default_task_prompts() -> dict[str, str]:
    """从内置注册表派生 task 类默认 prompt（兼容旧 ``_DEFAULT_PROMPTS`` 用法）。"""
    # 延迟 import 避免模块加载顺序问题
    from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS

    return {b.key: b.template for b in BUILTIN_PROMPTS if b.category == "task"}


# 兼容别名：旧代码 ``from prompt_registry import _DEFAULT_PROMPTS``
_DEFAULT_PROMPTS = _build_default_task_prompts()


def _validate_task_type(task_type: str) -> None:
    """校验 task_type 是否合法（仅 task 类需要）。"""
    if task_type not in ALL_TASK_TYPES:
        raise ValueError(
            f"Invalid task_type: {task_type!r}. "
            f"Must be one of: {', '.join(ALL_TASK_TYPES)}"
        )


def _validate_placeholders(template_text: str, required: list[str]) -> None:
    """校验模板含指定的必须占位符，且 ``str.format`` 语法合法。"""
    formatter = string.Formatter()
    try:
        field_names = {
            fname for _, fname, _, _ in formatter.parse(template_text) if fname
        }
    except (ValueError, IndexError):
        raise ValueError("Prompt template contains invalid format placeholders.")

    missing = set(required) - field_names
    if missing:
        raise ValueError(
            f"Prompt template is missing required placeholder(s): {', '.join(missing)}. "
            f"This category requires {{{', '.join(missing)}}}."
        )


def _validate_template(template_text: str) -> None:
    """旧接口：默认要求 ``{message}`` 占位符。"""
    _validate_placeholders(template_text, ["message"])


def _validate_for_category(category: str, key: str, template_text: str) -> None:
    """按 category/key 校验模板。

    task 类额外校验 task_type 合法性；各类按 ``required_placeholders_for`` 要求占位符
    （如 system 类要求 ``[]``，risk 类要求 ``[message, answer]``）。
    """
    if category == "task":
        _validate_task_type(key)
    required = required_placeholders_for(category, key)
    _validate_placeholders(template_text, required)


def _extract_version_id(mapping: Any, category: str, key: str) -> str | None:
    """从 AgentPromptSet JSON 提取版本 ID，兼容新旧两种 schema。

    新 schema（嵌套）：``{"task": {"knowledge_qa": "<id>"}, "system": {...}}``
    旧 schema（扁平）：``{"knowledge_qa": "<id>"}`` —— 仅 task 类，顶层值为字符串。
    """
    if not isinstance(mapping, dict):
        return None
    sub = mapping.get(category)
    if isinstance(sub, dict):
        return sub.get(key)
    if category == "task":
        val = mapping.get(key)
        if isinstance(val, str):
            return val
    return None


class PromptRegistry:
    """运行时 Prompt 解析与版本管理。

    用法::

        registry = PromptRegistry(db)
        # task 类（兼容旧接口）
        template = await registry.resolve(tenant_id, "knowledge_qa")
        # 任意层
        template = await registry.resolve_prompt("system", "system_constraint", tenant_id)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def resolve_prompt(
        self,
        category: str,
        key: str,
        tenant_id: str,
        agent_id: str | None = None,
    ) -> str:
        """通用三级回退解析任意层 prompt。

        顺序：Agent prompt_set 映射 → tenant active 版本 → 内置默认常量。
        """
        if agent_id:
            mapped = await self._resolve_agent_prompt_version(agent_id, category, key)
            if mapped is not None:
                logger.debug(
                    "Resolved agent prompt: agent=%s %s/%s version_id=%s",
                    agent_id, category, key, mapped.id,
                )
                return mapped.template_text

        stmt = select(PromptVersion).where(
            PromptVersion.tenant_id == tenant_id,
            PromptVersion.prompt_category == category,
            # COALESCE 兼容 prompt_key IS NULL 的旧行（回退到 task_type）
            func.coalesce(PromptVersion.prompt_key, PromptVersion.task_type) == key,
            PromptVersion.status == "active",
        ).limit(1)
        version = (await self.db.execute(stmt)).scalar_one_or_none()
        if version is not None:
            logger.debug(
                "Resolved tenant prompt: tenant=%s %s/%s version_id=%s",
                tenant_id, category, key, version.id,
            )
            return version.template_text

        builtin = get_builtin(category, key)
        if builtin is not None:
            logger.debug("Using builtin prompt: %s/%s", category, key)
            return builtin.template

        raise ValueError(f"No prompt found for {category}/{key}")

    async def resolve(self, tenant_id: str, task_type: str) -> str:
        """解析租户+任务类型的活跃 task prompt（兼容旧接口）。"""
        return await self.resolve_prompt("task", task_type, tenant_id)

    async def resolve_for_agent(
        self, agent_id: str | None, tenant_id: str, task_type: str
    ) -> str:
        """解析 Agent 作用域的 task prompt（兼容旧接口）。"""
        return await self.resolve_prompt("task", task_type, tenant_id, agent_id)

    async def _resolve_agent_prompt_version(
        self, agent_id: str, category: str, key: str
    ) -> PromptVersion | None:
        """从 Agent prompt_set 映射里取出 (category, key) → PromptVersion。"""
        from sales_agent.models.agent import Agent
        from sales_agent.models.agent_prompt_set import AgentPromptSet

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
        version_id = _extract_version_id(mapping, category, key)
        if not version_id:
            return None
        return (
            await self.db.execute(
                select(PromptVersion).where(PromptVersion.id == version_id)
            )
        ).scalar_one_or_none()

    async def create_version(
        self,
        tenant_id: str,
        task_type: str,
        template_text: str,
        description: str = "",
        version: str = "",
        prompt_category: str = "task",
        prompt_key: str | None = None,
    ) -> PromptVersion:
        """创建一个 draft 版本的 prompt。

        Args:
            task_type: task 类即 task_type；非 task 类可传任意值（仅用于兼容旧位置参数，
                实际定位用 prompt_category + prompt_key）。
            prompt_category: task / system / router / risk / coach（默认 task）。
            prompt_key: 该类别下的具体标识；为 None 时 task 类取 task_type，否则取该值。
        """
        category = prompt_category
        key = prompt_key or (task_type if category == "task" else task_type)
        _validate_for_category(category, key, template_text)

        placeholders = required_placeholders_for(category, key)
        pv = PromptVersion(
            tenant_id=tenant_id,
            prompt_category=category,
            prompt_key=key,
            task_type=task_type if category == "task" else None,
            version=version,
            status="draft",
            template_text=template_text,
            description=description,
            required_placeholders_json=json.dumps(placeholders, ensure_ascii=False),
        )
        self.db.add(pv)
        await self.db.flush()
        return pv

    async def activate_version(self, tenant_id: str, version_id: str) -> PromptVersion:
        """激活一个 draft 版本。

        同一 tenant + (category, key) 的当前 active 版本会被自动归档。
        """
        pv = await self._get_version_with_tenant_check(version_id, tenant_id)
        if pv is None:
            raise ValueError(f"Prompt version not found: {version_id!r}")
        if pv.status != "draft":
            raise ValueError(
                f"Only draft versions can be activated, current status: {pv.status!r}"
            )

        # 归档同 tenant + (category, key) 的当前 active 版本
        target_key = pv.prompt_key or pv.task_type
        stmt = select(PromptVersion).where(
            PromptVersion.tenant_id == tenant_id,
            PromptVersion.prompt_category == pv.prompt_category,
            func.coalesce(PromptVersion.prompt_key, PromptVersion.task_type) == target_key,
            PromptVersion.status == "active",
        )
        result = await self.db.execute(stmt)
        current_active = result.scalar_one_or_none()
        if current_active is not None and current_active.id != pv.id:
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
        """更新一个 draft 版本的内容（按其 category 校验占位符）。"""
        pv = await self._get_version_with_tenant_check(version_id, tenant_id)
        if pv is None:
            raise ValueError(f"Prompt version not found: {version_id!r}")
        if pv.status != "draft":
            raise ValueError("Only draft versions can be updated.")

        if template_text is not None:
            key = pv.prompt_key or pv.task_type or ""
            _validate_for_category(pv.prompt_category, key, template_text)
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
        prompt_category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromptVersion], int]:
        """列出租户的 prompt 版本。返回 (versions, total)。

        可按 task_type（task 类）或 prompt_category（任意层）过滤。
        """
        conditions = [PromptVersion.tenant_id == tenant_id]
        if prompt_category is not None:
            conditions.append(PromptVersion.prompt_category == prompt_category)
        if task_type is not None:
            _validate_task_type(task_type)
            conditions.append(PromptVersion.task_type == task_type)
        if status is not None:
            conditions.append(PromptVersion.status == status)

        from sqlalchemy import func

        count_stmt = select(func.count()).select_from(PromptVersion).where(*conditions)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

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
