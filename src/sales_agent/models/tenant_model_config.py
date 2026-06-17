"""租户模型配置表 — 不保存明文 API key。"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class TenantModelConfig(TimestampMixin, Base):
    __tablename__ = "tenant_model_configs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    chat_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    def __repr__(self) -> str:
        return f"<TenantModelConfig(tenant={self.tenant_id}, provider={self.provider})>"
