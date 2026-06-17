"""SecretResolver 和 TenantRuntime 单元测试。"""

import os
import pytest

from sales_agent.core.secret_resolver import (
    resolve_secret,
    key_fingerprint,
    sanitize_log_dict,
    SecretResolutionError,
)


class TestSecretResolver:
    def test_resolve_env_success(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY_123", "sk-test-value")
        result = resolve_secret("env:TEST_KEY_123")
        assert result == "sk-test-value"

    def test_resolve_env_missing(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_KEY_XYZ", raising=False)
        with pytest.raises(SecretResolutionError):
            resolve_secret("env:NONEXISTENT_KEY_XYZ")

    def test_resolve_empty_ref(self):
        with pytest.raises(SecretResolutionError):
            resolve_secret("")

    def test_resolve_unsupported_type(self):
        with pytest.raises(SecretResolutionError) as exc_info:
            resolve_secret("vault:my_secret")
        assert "unsupported" in str(exc_info.value).lower()


class TestKeyFingerprint:
    def test_returns_8_char_hex(self):
        fp = key_fingerprint("sk-test-key-12345")
        assert len(fp) == 8
        assert all(c in "0123456789abcdef" for c in fp)

    def test_empty_key(self):
        assert key_fingerprint("") == "none"

    def test_deterministic(self):
        assert key_fingerprint("same-key") == key_fingerprint("same-key")

    def test_different_keys_different_fingerprints(self):
        assert key_fingerprint("key-a") != key_fingerprint("key-b")


class TestSanitizeLogDict:
    def test_removes_api_key(self):
        data = {"model": "gpt-4", "api_key": "sk-secret-123"}
        cleaned = sanitize_log_dict(data)
        assert cleaned["model"] == "gpt-4"
        assert "sk-secret-123" not in cleaned["api_key"]
        assert "fingerprint" in cleaned["api_key"]

    def test_removes_authorization(self):
        data = {"url": "/v1/chat", "authorization": "Bearer sk-secret"}
        cleaned = sanitize_log_dict(data)
        assert cleaned["url"] == "/v1/chat"
        assert "sk-secret" not in cleaned["authorization"]

    def test_nested_dict(self):
        data = {"config": {"api_key": "secret", "model": "gpt-4"}}
        cleaned = sanitize_log_dict(data)
        assert "secret" not in cleaned["config"]["api_key"]
        assert cleaned["config"]["model"] == "gpt-4"

    def test_no_sensitive_data(self):
        data = {"model": "gpt-4", "temperature": 0.3}
        cleaned = sanitize_log_dict(data)
        assert cleaned == data


class TestTenantMismatch:
    def test_dedicated_mode_match(self):
        from sales_agent.core.tenant_runtime import TenantRuntime
        runtime = TenantRuntime(
            tenant_id="taishan",
            tenant_name="A",
            deployment_mode="dedicated",
        )
        assert runtime.check_tenant_match("taishan") is True

    def test_dedicated_mode_mismatch(self):
        from sales_agent.core.tenant_runtime import TenantRuntime
        runtime = TenantRuntime(
            tenant_id="taishan",
            tenant_name="A",
            deployment_mode="dedicated",
        )
        assert runtime.check_tenant_match("tenant_b") is False

    def test_shared_mode_no_check(self):
        from sales_agent.core.tenant_runtime import TenantRuntime
        runtime = TenantRuntime(
            tenant_id="taishan",
            tenant_name="A",
            deployment_mode="shared",
        )
        assert runtime.check_tenant_match("tenant_b") is True


class TestDebugInfo:
    def test_debug_info_no_plaintext_key(self):
        from sales_agent.core.tenant_runtime import TenantRuntime
        runtime = TenantRuntime(
            tenant_id="taishan",
            tenant_name="A",
            deployment_mode="dedicated",
            api_key_ref="env:MODEL_API_KEY",
            api_key_fingerprint="a1b2c3d4",
            provider="openai_compatible",
            base_url="https://api.example.com/v1",
            base_url_host="api.example.com",
            chat_model="qwen-plus",
            embedding_model="text-embedding-v3",
        )
        info = runtime.get_debug_info()
        assert "api_key" not in str(info).lower() or "api_key_ref" in str(info)
        assert "api_key_fingerprint" in info
        assert info["api_key_fingerprint"] == "a1b2c3d4"
        # 绝对不能出现明文 key
        assert "sk-" not in str(info)

    def test_log_info_same_as_debug(self):
        from sales_agent.core.tenant_runtime import TenantRuntime
        runtime = TenantRuntime(
            tenant_id="t1",
            tenant_name="T1",
            deployment_mode="dedicated",
        )
        assert runtime.get_log_info() == runtime.get_debug_info()
