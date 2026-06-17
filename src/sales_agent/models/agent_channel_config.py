"""Agent 渠道配置 — 不保存明文密钥。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentChannelConfig(TimestampMixin, Base):
    """Agent 的渠道（钉钉/未来其它渠道）绑定。

    config_json: 非敏感配置（corp_id、机器人名称、回调路径等）。
    secret_refs_json: 仅保存密钥引用（env:VAR / secret:ref），绝不存明文。
    status: not_configured / configured / verified / disabled。
    """

    __tablename__ = "agent_channel_configs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(
        Text, nullable=False, default="dingtalk"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="not_configured"
    )
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 仅 secret 引用，例如 {"robot_app_key":"env:DT_APP_KEY"}；禁止明文
    secret_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return f"<AgentChannelConfig(id={self.id}, agent={self.agent_id}, channel={self.channel}, status={self.status})>"
