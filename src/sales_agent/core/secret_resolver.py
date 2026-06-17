"""密钥解析器 — 根据 api_key_ref 格式解析密钥。

v0 支持 env:VAR_NAME 格式。
后续可扩展 file:/path、secret:name、kms:name。
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)


class SecretResolutionError(Exception):
    """密钥解析失败。"""

    def __init__(self, ref: str, reason: str = ""):
        self.ref = ref
        self.reason = reason
        super().__init__(f"Failed to resolve secret ref '{ref}': {reason}")


def resolve_secret(ref: str) -> str:
    """根据 ref 格式解析密钥。

    Args:
        ref: 密钥引用，格式为 "env:VAR_NAME" 等

    Returns:
        解析后的明文密钥

    Raises:
        SecretResolutionError: 解析失败
    """
    if not ref:
        raise SecretResolutionError("", "ref is empty")

    if ref.startswith("env:"):
        var_name = ref[4:]
        value = os.environ.get(var_name, "")
        if not value:
            raise SecretResolutionError(ref, f"environment variable {var_name} is empty or not set")
        return value

    # 后续扩展点：
    # elif ref.startswith("file:"):
    #     path = ref[5:]
    #     return Path(path).read_text().strip()
    # elif ref.startswith("secret:"):
    #     ...
    # elif ref.startswith("kms:"):
    #     ...

    raise SecretResolutionError(ref, f"unsupported ref type: {ref}")


def key_fingerprint(api_key: str) -> str:
    """计算 API key 指纹（sha256 前 8 位），用于日志脱敏。"""
    if not api_key:
        return "none"
    return hashlib.sha256(api_key.encode()).hexdigest()[:8]


def sanitize_log_dict(data: dict, key_fields: set[str] | None = None) -> dict:
    """清理日志字典中的敏感字段。

    移除常见密钥字段，替换为指纹。
    """
    sensitive_keys = key_fields or {
        "api_key", "apikey", "api_key_value",
        "authorization", "password", "secret",
        "token", "access_token", "aes_key",
    }
    cleaned = {}
    for k, v in data.items():
        if k.lower() in sensitive_keys:
            if isinstance(v, str) and v:
                cleaned[k] = f"[fingerprint:{key_fingerprint(v)}]"
            else:
                cleaned[k] = "[redacted]"
        elif isinstance(v, dict):
            cleaned[k] = sanitize_log_dict(v, sensitive_keys)
        else:
            cleaned[k] = v
    return cleaned
