"""Agent 运行追踪模型。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentRun(TimestampMixin, Base):
    """Agent 运行记录。

    每次 chat 请求创建一条 run，包含总体状态和延迟。
    """

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="running"
    )  # running / completed / failed
    total_latency_ms: Mapped[int] = mapped_column(nullable=True)
    route_confidence: Mapped[float] = mapped_column(nullable=True)
    error_json: Mapped[str] = mapped_column(Text, nullable=True)


class AgentRunStep(TimestampMixin, Base):
    """Agent 运行步骤记录。

    步骤名称对应 ChatPipeline 的各阶段：
    validation / tenant_resolve / context_load / routing /
    retrieval / generation / risk_check / logging。
    """

    __tablename__ = "agent_run_steps"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    agent_run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_order: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="completed"
    )  # completed / failed / skipped
    latency_ms: Mapped[int] = mapped_column(nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_summary: Mapped[str] = mapped_column(Text, nullable=True)
