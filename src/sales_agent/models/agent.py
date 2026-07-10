"""Agent Instance 模型 — 一对一管理单元和克隆起点。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class Agent(TimestampMixin, Base):
    """Agent Instance。

    产品层的一等实体：一个 Agent 对应一组 prompt / knowledge scope /
    risk policy / eval suite / channel 绑定 + 运行状态。

    tenant_id 仍是主隔离边界；agent_id 在 tenant 内缩小作用域。
    """

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="sales_assistant"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # draft / active / paused / archived
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", index=True)
    # 克隆来源 Agent（None = 原生创建）
    source_agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    # 模型/运行时配置引用。"runtime" = 使用 TenantRuntime（dedicated 模式默认）
    model_config_ref: Mapped[str] = mapped_column(Text, nullable=False, default="runtime")
    # 子配置表的外键（通过 id 文本引用，不强制 FK 约束以保持迁移弹性）
    knowledge_scope_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_policy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_suite_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # UI/页面配置 + 特性开关 + 激活豁免记录
    feature_flags_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # 该 tenant 是否为默认 Agent（每个 tenant 恰好一个，用于兼容 tenant-only 调用）
    is_tenant_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, index=True
    )

    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_agents_tenant_default", "tenant_id", "is_tenant_default"),
        Index("ix_agents_tenant_status", "tenant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, tenant={self.tenant_id}, name={self.name}, status={self.status})>"
