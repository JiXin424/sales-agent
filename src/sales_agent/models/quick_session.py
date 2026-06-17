"""快捷入口多轮对话会话状态（小赢欣赏 / 卡点破框）。

用户点击钉钉快捷入口按钮开始一个会话；之后在钉钉单聊里的回复由
streaming_handler 顶部拦截，推进状态机直到完成出卡。状态落库，
容器重启不丢失（区别于 Omni 的纯内存 MVP）。
"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class QuickSession(TimestampMixin, Base):
    """一次快捷入口触发的多轮教练对话会话。"""

    __tablename__ = "quick_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="dingtalk")
    # 钉钉侧用户 ID（staffId 格式），用于把后续回复匹配回会话
    external_user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # small_win_appreciation | sales_block_breakthrough
    session_type: Mapped[str] = mapped_column(Text, nullable=False)
    # 当前阶段（small_win: small_win→strength→gratitude→energy；
    # breakthrough: awaiting_blocker→awaiting_split→awaiting_possibilities）
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # active | completed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active", index=True)

    __table_args__ = (
        Index("ix_quick_session_active", "tenant_id", "external_user_id", "status"),
    )
