"""质量审查队列模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class ReviewItem(TimestampMixin, Base):
    """审查队列条目。

    当会话出现负反馈、高风险、低评分、模型错误、检索缺失等情况时自动入队，
    也支持管理员手动标记。
    """

    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    feedback_id: Mapped[str] = mapped_column(Text, nullable=True)
    agent_run_id: Mapped[str] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # negative_feedback / high_risk / low_score / model_error / retrieval_miss / manual_flag
    priority: Mapped[str] = mapped_column(
        Text, nullable=False, default="medium"
    )  # low / medium / high / critical
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open"
    )  # open / in_progress / resolved / ignored
    assignee: Mapped[str] = mapped_column(Text, nullable=True)
    notes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_review_items_tenant_status", "tenant_id", "status"),
    )
