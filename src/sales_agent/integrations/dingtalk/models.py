"""钉钉集成数据库模型。"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.models.base import Base, TimestampMixin, generate_id  # noqa: F811 — direct import avoids circular


class DingTalkInboundMessage(TimestampMixin, Base):
    """钉钉入站消息记录。"""

    __tablename__ = "dingtalk_inbound_messages"

    id: Mapped[str] = mapped_column(primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(index=True)
    corp_id: Mapped[str] = mapped_column()
    dingtalk_user_id: Mapped[str] = mapped_column()
    dingtalk_message_id: Mapped[str | None] = mapped_column(default=None)
    conversation_key: Mapped[str] = mapped_column()
    message_type: Mapped[str] = mapped_column()  # "text", "image", "unknown"
    text: Mapped[str | None] = mapped_column(default=None)
    raw_event_json: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column()  # "pending", "processing", "completed", "rejected", "duplicate"
    error_json: Mapped[str | None] = mapped_column(default=None)


class DingTalkOutboundMessage(TimestampMixin, Base):
    """钉钉出站消息记录。"""

    __tablename__ = "dingtalk_outbound_messages"

    id: Mapped[str] = mapped_column(primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(index=True)
    corp_id: Mapped[str] = mapped_column()
    dingtalk_user_id: Mapped[str] = mapped_column()
    conversation_id: Mapped[str] = mapped_column()
    agent_conversation_id: Mapped[str | None] = mapped_column(default=None)
    text: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column()  # "pending", "sending", "sent", "failed", "skipped_duplicate"
    retry_count: Mapped[int] = mapped_column(default=0)
    error_json: Mapped[str | None] = mapped_column(default=None)


class DingTalkUserMapping(TimestampMixin, Base):
    """钉钉用户到内部用户的映射。"""

    __tablename__ = "dingtalk_user_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dingtalk_user_id", name="uq_dingtalk_user_tenant"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(index=True)
    dingtalk_corp_id: Mapped[str] = mapped_column()
    dingtalk_user_id: Mapped[str] = mapped_column()
    internal_user_id: Mapped[str] = mapped_column()
    display_name: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="active")  # "active", "disabled"
