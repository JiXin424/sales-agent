"""模型调用日志记录 — 脱敏，不保存明文 key。"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.model_call_log import ModelCallLog
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


async def log_model_call(
    db: AsyncSession | None,
    *,
    tenant_id: str,
    provider: str,
    request_type: str,  # "chat" / "embedding"
    status: str,  # "success" / "failed"
    base_url_host: str = "",
    chat_model: str = "",
    embedding_model: str = "",
    api_key_ref: str = "",
    api_key_fingerprint: str = "",
    latency_ms: int | None = None,
    error_code: str = "",
) -> None:
    """记录模型调用日志（脱敏）。

    失败不影响主流程。
    """
    try:
        if db is None:
            return
        log = ModelCallLog(
            id=generate_id(),
            tenant_id=tenant_id,
            provider=provider,
            base_url_host=base_url_host,
            chat_model=chat_model,
            embedding_model=embedding_model,
            api_key_ref=api_key_ref,
            api_key_fingerprint=api_key_fingerprint,
            request_type=request_type,
            status=status,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        db.add(log)
        await db.flush()
    except Exception as e:
        # 日志写入失败不影响主流程
        logger.error("Failed to log model call: %s", e)


class ModelCallTimer:
    """计时器上下文，用于记录模型调用延迟。"""

    def __init__(self):
        self.start_time: float = 0
        self.latency_ms: int = 0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.latency_ms = int((time.time() - self.start_time) * 1000)
