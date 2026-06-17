"""数据库模型基类。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from sales_agent.core.database import Base


def generate_id() -> str:
    """生成唯一 ID。"""
    return uuid.uuid4().hex[:16]


def utcnow() -> str:
    """返回 UTC ISO 格式时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


class TimestampMixin:
    """created_at / updated_at 自动时间戳 mixin。"""

    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utcnow, server_default=func.now(), onupdate=utcnow
    )
