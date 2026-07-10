"""Agent Instance 服务：CRUD、状态转换、所有权校验。

Agent 是产品层一等实体，位于 tenant 之上。tenant_id 仍是主隔离边界。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent import Agent
from sales_agent.models.agent_channel_config import AgentChannelConfig
from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope

logger = logging.getLogger(__name__)

# 合法状态机
_VALID_STATUS = {"draft", "active", "paused", "archived"}
# 合法状态转换
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "archived"},
    "active": {"paused", "archived"},
    "paused": {"active", "archived"},
    "archived": set(),  # 终态
}


class AgentNotFoundError(Exception):
    pass


class AgentOwnershipError(Exception):
    pass


class AgentStatusError(Exception):
    pass


def _agent_to_dict(agent: Agent) -> dict[str, Any]:
    """把 Agent ORM 行转为 API 响应 dict（含 feature_flags 解析）。"""
    try:
        feature_flags = json.loads(agent.feature_flags_json) if agent.feature_flags_json else {}
    except json.JSONDecodeError:
        feature_flags = {}
    return {
        "id": agent.id,
        "tenant_id": agent.tenant_id,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "description": agent.description,
        "status": agent.status,
        "source_agent_id": agent.source_agent_id,
        "model_config_ref": agent.model_config_ref,
        "knowledge_scope_id": agent.knowledge_scope_id,
        "risk_policy_id": agent.risk_policy_id,
        "eval_suite_id": agent.eval_suite_id,
        "feature_flags": feature_flags,
        "is_tenant_default": bool(agent.is_tenant_default),
        "created_by": agent.created_by,
        "activated_at": agent.activated_at,
        "archived_at": agent.archived_at,
        "created_at": agent.created_at or "",
        "updated_at": agent.updated_at or "",
    }


class AgentService:
    """Agent CRUD + 状态管理。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_agent(self, agent_id: str) -> Agent:
        agent = await self._get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return agent

    async def get_agent_for_tenant(self, agent_id: str, tenant_id: str) -> Agent:
        """获取 Agent 并校验归属 tenant。"""
        agent = await self._get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if agent.tenant_id != tenant_id:
            raise AgentOwnershipError(agent_id, tenant_id)
        return agent

    async def _get(self, agent_id: str) -> Agent | None:
        return (
            await self.db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()

    async def list_agents(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        agent_type: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        if tenant_id:
            conditions.append(Agent.tenant_id == tenant_id)
        if status:
            conditions.append(Agent.status == status)
        if agent_type:
            conditions.append(Agent.agent_type == agent_type)
        if q:
            conditions.append(Agent.name.ilike(f"%{q}%"))

        count_stmt = select(func.count()).select_from(Agent)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Agent)
            .order_by(Agent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [_agent_to_dict(a) for a in rows], total

    async def create_agent(self, req: dict[str, Any]) -> dict[str, Any]:
        """创建 draft Agent，并初始化 prompt_set / knowledge_scope 空壳。"""
        tenant_id = req["tenant_id"]
        name = req["name"]
        agent = Agent(
            tenant_id=tenant_id,
            name=name,
            agent_type=req.get("agent_type", "sales_assistant"),
            description=req.get("description", ""),
            status="draft",
            model_config_ref=req.get("model_config_ref", "runtime"),
            feature_flags_json=json.dumps(req.get("feature_flags", {}), ensure_ascii=False),
            is_tenant_default=False,
            created_by=req.get("created_by"),
        )
        self.db.add(agent)
        await self.db.flush()

        prompt_set = AgentPromptSet(
            agent_id=agent.id, tenant_id=tenant_id, name="default",
            task_prompt_versions_json="{}",
        )
        self.db.add(prompt_set)
        await self.db.flush()

        scope = AgentKnowledgeScope(
            agent_id=agent.id, tenant_id=tenant_id,
            mode=req.get("knowledge_scope_mode", "tenant_all"),
            document_ids_json=json.dumps(req.get("document_ids", [])),
            source_file_ids_json=json.dumps(req.get("source_file_ids", [])),
        )
        self.db.add(scope)
        await self.db.flush()
        agent.knowledge_scope_id = scope.id

        await self.db.flush()
        return _agent_to_dict(agent)

    async def update_agent(self, agent_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        agent = await self.get_agent(agent_id)
        if agent.status == "archived":
            raise AgentStatusError("archived", "Archived agents are read-only")
        if "name" in updates and updates["name"] is not None:
            agent.name = updates["name"]
        if "description" in updates and updates["description"] is not None:
            agent.description = updates["description"]
        if "feature_flags" in updates and updates["feature_flags"] is not None:
            agent.feature_flags_json = json.dumps(updates["feature_flags"], ensure_ascii=False)
        await self.db.flush()
        return _agent_to_dict(agent)

    async def transition_status(
        self, agent_id: str, target: str
    ) -> dict[str, Any]:
        """状态转换：draft→active→paused→archived。"""
        if target not in _VALID_STATUS:
            raise AgentStatusError(target, f"Invalid status: {target}")
        agent = await self.get_agent(agent_id)
        cur = agent.status
        if cur == target:
            return _agent_to_dict(agent)
        if target not in _TRANSITIONS.get(cur, set()):
            raise AgentStatusError(target, f"Cannot transition {cur} -> {target}")
        agent.status = target
        if target == "active" and not agent.activated_at:
            agent.activated_at = _utcnow_iso()
        if target == "archived" and not agent.archived_at:
            agent.archived_at = _utcnow_iso()
        await self.db.flush()
        return _agent_to_dict(agent)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def load_agent_document_scope(
    db: AsyncSession, agent: Agent
) -> set[str] | None:
    """返回 Agent 知识作用域允许的 document_id 集合。

    - tenant_all → None（不限，使用 tenant 全量）。
    - document_subset → 显式 document_ids（可能为空集 = 无知识）。
    - source_file_subset → 由 source_file_ids 解析出的 document_ids。
    """
    if not agent.knowledge_scope_id:
        return None  # 未配置 → 回退 tenant 全量
    from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope
    from sales_agent.models.document import Document, SourceFile
    scope = (
        await db.execute(
            select(AgentKnowledgeScope).where(AgentKnowledgeScope.id == agent.knowledge_scope_id)
        )
    ).scalar_one_or_none()
    if scope is None or scope.mode == "tenant_all":
        return None
    if scope.mode == "document_subset":
        return set(json.loads(scope.document_ids_json or "[]"))
    # source_file_subset：解析 source_file → document
    source_file_ids = json.loads(scope.source_file_ids_json or "[]")
    if not source_file_ids:
        return set()
    # 通过 SourceFile.normalized_markdown_path / stored_path 关联 Document.source_path
    sf_rows = (
        await db.execute(
            select(SourceFile).where(SourceFile.id.in_(source_file_ids))
        )
    ).scalars().all()
    paths = {sf.normalized_markdown_path or sf.stored_path for sf in sf_rows if sf}
    if not paths:
        return set()
    doc_rows = (
        await db.execute(
            select(Document.id).where(
                Document.tenant_id == agent.tenant_id,
                Document.source_path.in_(paths),
            )
        )
    ).scalars().all()
    return set(doc_rows)


async def resolve_tenant_agent_id(
    db: AsyncSession, tenant_id: str, agent_id: str | None
) -> Agent:
    """解析 (tenant_id, agent_id) → Agent。

    agent_id 为 None 时回退到 tenant 默认 Agent（向后兼容 tenant-only 调用）。
    校验 Agent 归属 tenant。
    """
    if agent_id:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if agent.tenant_id != tenant_id:
            raise AgentOwnershipError(agent_id, tenant_id)
        return agent

    # 回退到默认 Agent
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.is_tenant_default == True,  # noqa: E712
            ).limit(1)
        )
    ).scalar_one_or_none()
    if agent is None:
        # 兜底：取该 tenant 任意 active Agent
        agent = (
            await db.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant_id,
                    Agent.status == "active",
                ).limit(1)
            )
        ).scalar_one_or_none()
    if agent is None:
        raise AgentNotFoundError(f"default for tenant {tenant_id}")
    return agent
