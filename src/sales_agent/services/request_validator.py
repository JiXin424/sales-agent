"""请求校验服务。"""

from __future__ import annotations

from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import (
    MessageEmptyError,
    MessageTooLongError,
    ValidationError,
)


def validate_chat_request(
    tenant_id: str | None,
    user_id: str | None,
    message: str | None,
) -> None:
    """校验 chat 请求参数。

    Raises:
        ValidationError: tenant_id 或 user_id 缺失
        MessageEmptyError: message 为空
        MessageTooLongError: message 超长
    """
    if not tenant_id:
        raise ValidationError("tenant_id is required")
    if not user_id:
        raise ValidationError("user_id is required")
    if not message or not message.strip():
        raise MessageEmptyError()

    settings = get_settings()
    if len(message) > settings.app.max_message_chars:
        raise MessageTooLongError(settings.app.max_message_chars)
