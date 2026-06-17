"""会话模型。"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_message: Mapped[str] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(Text, nullable=True)
    task_confidence: Mapped[float] = mapped_column(nullable=True)
    answer: Mapped[str] = mapped_column(Text, nullable=True)
    risk_json: Mapped[str] = mapped_column(Text, nullable=True)
    sources_json: Mapped[str] = mapped_column(Text, nullable=True)
    model_config_json: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="completed")
    error_json: Mapped[str] = mapped_column(Text, nullable=True)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, tenant_id={self.tenant_id})>"


class RetrievalLog(TimestampMixin, Base):
    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<RetrievalLog(id={self.id}, conversation_id={self.conversation_id})>"


class ConversationMessage(TimestampMixin, Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ConversationMessage(id={self.id}, role={self.role})>"


class ConversationSummary(TimestampMixin, Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=True)
    risk_notes_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ConversationSummary(id={self.id}, conv={self.conversation_id})>"
