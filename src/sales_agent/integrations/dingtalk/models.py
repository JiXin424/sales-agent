"""钉钉集成数据库模型。"""

from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint
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


class DingTalkCardCallbackEvent(TimestampMixin, Base):
    """钉钉互动卡片按钮回调原始事件（点赞 / 点踩 / 问题反馈多选等）。

    capture-first：先把钉钉回调载荷原样落库，「原始载荷 → :class:`Feedback`」
    的映射由后续里程碑基于真实样本处理。``processed`` 标记回放进度。

    归因无需映射表——回调载荷带 ``dingtalk_user_id``，结合固定的 ``tenant_id``
    可确定性重算 conversation_id（见 conversation_mapper.generate_conversation_id）。
    """

    __tablename__ = "dingtalk_card_callback_events"
    __table_args__ = (
        Index("ix_dt_card_cb_tenant_created", "tenant_id", "created_at"),
        Index("ix_dt_card_cb_card_instance", "card_instance_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column()
    dingtalk_user_id: Mapped[str] = mapped_column(default="")
    card_instance_id: Mapped[str | None] = mapped_column(default=None)  # 钉钉 outTrackId
    corp_id: Mapped[str | None] = mapped_column(default=None)
    space_id: Mapped[str | None] = mapped_column(default=None)
    content_json: Mapped[str] = mapped_column(default="{}")  # 原始 content（表单/按钮提交值）
    extension_json: Mapped[str] = mapped_column(default="{}")  # 原始 extension（预置私有数据）
    raw_json: Mapped[str] = mapped_column(default="{}")  # 完整 callback.data 兜底
    processed: Mapped[bool] = mapped_column(default=False)  # M2 映射回放标记
