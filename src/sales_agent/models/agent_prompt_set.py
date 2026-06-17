"""Agent Prompt 集合 — 任务类型到 prompt_version 的映射。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentPromptSet(TimestampMixin, Base):
    """Agent 的活跃 prompt 集合。

    task_prompt_versions_json: {"knowledge_qa": "<prompt_version_id>", ...}
    一个 Agent 通常持有一个 prompt set；复制 Agent 时新建独立 set。
    """

    __tablename__ = "agent_prompt_sets"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    task_prompt_versions_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )

    def __repr__(self) -> str:
        return f"<AgentPromptSet(id={self.id}, agent={self.agent_id})>"
