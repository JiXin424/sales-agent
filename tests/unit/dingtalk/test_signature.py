"""钉钉签名验证单元测试。"""

import base64
import hashlib
import hmac

import pytest

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.signature import DingTalkSignatureVerifier


class TestDingTalkSignatureVerifier:

    def _make_config(self, **kwargs) -> DingTalkConfig:
        defaults = {
            "app_secret": "test_secret_123",
            "encrypt_token": "",
            "aes_key": "",
        }
        defaults.update(kwargs)
        return DingTalkConfig(enabled=True, **defaults)

    def _compute_sign(self, timestamp: str, app_secret: str) -> str:
        """用与 verifer 相同的算法计算签名。"""
        string_to_sign = f"{timestamp}\n{app_secret}"
        hmac_code = hmac.new(
            app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def test_verify_valid_signature(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        timestamp = "1700000000000"
        sign = self._compute_sign(timestamp, config.app_secret)
        assert verifier.verify(timestamp, sign) is True

    def test_verify_invalid_signature(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        assert verifier.verify("1700000000000", "invalid_sign_value") is False

    def test_verify_empty_timestamp(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        assert verifier.verify("", "some_sign") is False

    def test_verify_empty_sign(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        assert verifier.verify("1700000000000", "") is False

    def test_verify_no_app_secret(self):
        config = self._make_config(app_secret="")
        verifier = DingTalkSignatureVerifier(config)
        assert verifier.verify("1700000000000", "some_sign") is False

    def test_sanitize_raw_event_removes_sensitive_keys(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        raw = {
            "msgtype": "text",
            "appSecret": "should_be_removed",
            "token": "should_be_removed",
            "aes_key": "should_be_removed",
            "text": {"content": "hello"},
            "normal_field": "kept",
        }
        sanitized = verifier.sanitize_raw_event(raw)
        assert sanitized["appSecret"] == "[redacted]"
        assert sanitized["token"] == "[redacted]"
        assert sanitized["aes_key"] == "[redacted]"
        assert sanitized["normal_field"] == "kept"
        assert sanitized["text"]["content"] == "hello"

    def test_sanitize_nested_sensitive(self):
        config = self._make_config()
        verifier = DingTalkSignatureVerifier(config)
        raw = {
            "data": {"access_token": "secret123", "count": 5},
        }
        sanitized = verifier.sanitize_raw_event(raw)
        assert sanitized["data"]["access_token"] == "[redacted]"
        assert sanitized["data"]["count"] == 5
