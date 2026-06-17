"""模型调用日志表 — 不保存明文 key、prompt 全文、Authorization header。"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class ModelCallLog(TimestampMixin, Base):
    __tablename__ = "model_call_logs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    base_url_host: Mapped[str] = mapped_column(Text, nullable=True)
    chat_model: Mapped[str] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=True)
    api_key_ref: Mapped[str] = mapped_column(Text, nullable=True)
    api_key_fingerprint: Mapped[str] = mapped_column(Text, nullable=True)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)  # chat / embedding
    status: Mapped[str] = mapped_column(Text, nullable=False)  # success / failed
    latency_ms: Mapped[int] = mapped_column(nullable=True)
    error_code: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ModelCallLog(tenant={self.tenant_id}, type={self.request_type}, status={self.status})>"
