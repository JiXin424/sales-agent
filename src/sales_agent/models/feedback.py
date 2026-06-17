"""用户反馈模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class Feedback(TimestampMixin, Base):
    """用户反馈记录。

    独立表，关联 conversation 和 tenant，支持按租户/会话查询。
    """

    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[str] = mapped_column(Text, nullable=False)  # "up" / "down"
    feedback_text: Mapped[str] = mapped_column(Text, nullable=True)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    categories_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="open")  # open / reviewed / ignored

    __table_args__ = (
        Index(
            "ix_feedbacks_tenant_conversation",
            "tenant_id",
            "conversation_id",
        ),
    )
