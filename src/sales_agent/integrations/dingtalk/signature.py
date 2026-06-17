"""钉钉回调签名验证。

钉钉机器人回调验签算法：
1. 将 timestamp + "\\n" + app_secret 拼接为签名原文
2. 使用 HmacSHA256 对签名原文进行签名
3. Base64 编码后与请求中的 sign 字段比较

如启用加密回调，还需要 AES-256-CBC 解密事件体。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from sales_agent.integrations.dingtalk.config import DingTalkConfig

logger = logging.getLogger(__name__)


class DingTalkSignatureVerifier:
    """钉钉回调签名验证器。"""

    def __init__(self, config: DingTalkConfig):
        self._app_secret = config.app_secret
        self._encrypt_token = config.encrypt_token
        self._aes_key = config.aes_key

    def verify(self, timestamp: str, sign: str) -> bool:
        """验证钉钉回调签名。

        Args:
            timestamp: 请求头中的 timestamp
            sign: 请求头中的 sign

        Returns:
            签名是否通过
        """
        if not self._app_secret or not timestamp or not sign:
            logger.warning("Signature verification skipped: missing params")
            return False

        string_to_sign = f"{timestamp}\n{self._app_secret}"
        hmac_code = hmac.new(
            self._app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_sign = base64.b64encode(hmac_code).decode("utf-8")

        return hmac.compare_digest(expected_sign, sign)

    def decrypt(self, encrypt_payload: str) -> str:
        """解密 AES 加密的事件体（可选）。

        仅在配置了 aes_key 时启用。
        使用 pycryptodome 进行 AES-256-CBC 解密。

        Args:
            encrypt_payload: Base64 编码的加密数据

        Returns:
            解密后的 JSON 字符串

        Raises:
            ImportError: 未安装 pycryptodome
            ValueError: 解密失败
        """
        if not self._aes_key:
            raise ValueError("AES key not configured")

        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError:
            raise ImportError(
                "pycryptodome is required for encrypted callbacks. "
                "Install with: pip install pycryptodome"
            )

        # 钉钉 AES key 是 Base64 编码的 43 字符字符串，解码后为 32 字节
        aes_key_bytes = base64.b64decode(f"{self._aes_key}=")
        encrypted_data = base64.b64decode(encrypt_payload)

        # IV 是 aes_key 的前 16 字节
        iv = aes_key_bytes[:16]
        cipher = AES.new(aes_key_bytes, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(encrypted_data), AES.block_size)

        return decrypted.decode("utf-8")

    def sanitize_raw_event(self, raw_event: dict[str, Any]) -> dict[str, Any]:
        """脱敏原始事件，移除敏感字段。"""

        sensitive_keys = {
            "appsecret", "app_secret", "token", "aes_key",
            "access_token", "encrypt",
        }
        sanitized = {}
        for k, v in raw_event.items():
            if k.lower() in sensitive_keys:
                sanitized[k] = "[redacted]"
            elif isinstance(v, dict):
                sanitized[k] = self.sanitize_raw_event(v)
            else:
                sanitized[k] = v
        return sanitized
