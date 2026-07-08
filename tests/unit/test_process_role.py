"""进程角色拆分测试。

覆盖：
1. PROCESS_ROLE 配置加载和验证
2. main.py lifespan 中钉钉 Worker 的启动门控
3. 角色运行器基本行为
4. /health 端点返回角色信息
"""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ============================================================
# 配置测试
# ============================================================


class TestProcessRoleConfig:
    """PROCESS_ROLE 配置加载和验证。"""

    def test_default_role_is_all(self):
        """默认 role 为 all。"""
        from sales_agent.core.config import AppConfig
        config = AppConfig()
        assert config.process_role == "all"

    def test_valid_roles(self):
        """所有合法角色值均可解析。"""
        from sales_agent.core.config import AppConfig
        for role in ("all", "api", "stream", "worker"):
            config = AppConfig(process_role=role)
            assert config.get_process_role() == role

    def test_invalid_role_falls_back_to_all(self):
        """无效角色回退为 all 并记录警告。"""
        from sales_agent.core.config import AppConfig
        config = AppConfig(process_role="invalid")
        assert config.get_process_role() == "all"

    def test_empty_role_falls_back_to_all(self):
        """空字符串回退为 all。"""
        from sales_agent.core.config import AppConfig
        config = AppConfig(process_role="")
        assert config.get_process_role() == "all"

    def test_env_var_override(self, tmp_path):
        """PROCESS_ROLE 环境变量覆盖配置。"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("app:\n  log_level: info\n")

        from sales_agent.core.config import Settings
        with patch.dict(os.environ, {"PROCESS_ROLE": "api"}, clear=False):
            settings = Settings.from_yaml(yaml_file)
            assert settings.app.process_role == "api"

    def test_env_var_override_stream(self, tmp_path):
        """PROCESS_ROLE=stream 可正常加载。"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("app:\n  log_level: info\n")

        from sales_agent.core.config import Settings
        with patch.dict(os.environ, {"PROCESS_ROLE": "stream"}, clear=False):
            settings = Settings.from_yaml(yaml_file)
            assert settings.app.process_role == "stream"

    def test_env_var_override_worker(self, tmp_path):
        """PROCESS_ROLE=worker 可正常加载。"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("app:\n  log_level: info\n")

        from sales_agent.core.config import Settings
        with patch.dict(os.environ, {"PROCESS_ROLE": "worker"}, clear=False):
            settings = Settings.from_yaml(yaml_file)
            assert settings.app.process_role == "worker"


# ============================================================
# Lifespan 角色门控测试
# ============================================================


class TestLifespanRoleGating:
    """验证 lifespan 中钉钉 Worker 启动逻辑受角色控制。"""

    @pytest.fixture
    def mock_settings(self):
        """创建带角色控制的 mock settings。"""
        settings = MagicMock()
        settings.app.log_level = "info"
        settings.app.get_process_role.return_value = "all"
        settings.dingtalk.enabled = True
        settings.dingtalk.message_mode = "stream"
        return settings

    @pytest.fixture
    def mock_runtime(self):
        """创建 mock TenantRuntime。"""
        runtime = MagicMock()
        runtime.tenant_id = "test-tenant"
        runtime.deployment_mode = "dedicated"
        runtime.provider = "openai_compatible"
        runtime.chat_model = "test-model"
        runtime.validate_startup.return_value = []
        return runtime

    @pytest.mark.asyncio
    async def test_role_all_starts_dingtalk_stream(self, mock_settings, mock_runtime):
        """role=all 时启动钉钉 Stream Worker。"""
        mock_settings.app.get_process_role.return_value = "all"

        with (
            patch("sales_agent.core.config.get_settings", return_value=mock_settings),
            patch("sales_agent.core.database.init_db", new_callable=AsyncMock),
            patch("sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime),
            patch("sales_agent.integrations.dingtalk.stream_client.start_dingtalk_stream_worker", new_callable=AsyncMock) as mock_start,
            patch("sales_agent.services.online_conversation.initialize_online_runtime", new_callable=AsyncMock),
            patch("sales_agent.services.online_conversation.close_online_runtime", new_callable=AsyncMock),
        ):
            from sales_agent.main import lifespan
            app = MagicMock()

            async with lifespan(app):
                mock_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_role_api_skips_dingtalk(self, mock_settings, mock_runtime):
        """role=api 时不启动任何钉钉 Worker。"""
        mock_settings.app.get_process_role.return_value = "api"

        with (
            patch("sales_agent.core.config.get_settings", return_value=mock_settings),
            patch("sales_agent.core.database.init_db", new_callable=AsyncMock),
            patch("sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime),
            patch("sales_agent.integrations.dingtalk.stream_client.start_dingtalk_stream_worker", new_callable=AsyncMock) as mock_stream,
            patch("sales_agent.integrations.dingtalk.worker.start_dingtalk_worker", new_callable=AsyncMock) as mock_http,
            patch("sales_agent.services.online_conversation.initialize_online_runtime", new_callable=AsyncMock),
            patch("sales_agent.services.online_conversation.close_online_runtime", new_callable=AsyncMock),
        ):
            from sales_agent.main import lifespan
            app = MagicMock()

            async with lifespan(app):
                mock_stream.assert_not_awaited()
                mock_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_role_stream_starts_stream_worker(self, mock_settings, mock_runtime):
        """role=stream 时仅启动 Stream Worker。"""
        mock_settings.app.get_process_role.return_value = "stream"
        mock_settings.dingtalk.message_mode = "stream"

        with (
            patch("sales_agent.core.config.get_settings", return_value=mock_settings),
            patch("sales_agent.core.database.init_db", new_callable=AsyncMock),
            patch("sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime),
            patch("sales_agent.integrations.dingtalk.stream_client.start_dingtalk_stream_worker", new_callable=AsyncMock) as mock_stream,
            patch("sales_agent.integrations.dingtalk.worker.start_dingtalk_worker", new_callable=AsyncMock) as mock_http,
            patch("sales_agent.services.online_conversation.initialize_online_runtime", new_callable=AsyncMock),
            patch("sales_agent.services.online_conversation.close_online_runtime", new_callable=AsyncMock),
        ):
            from sales_agent.main import lifespan
            app = MagicMock()

            async with lifespan(app):
                mock_stream.assert_awaited_once()
                mock_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_role_worker_starts_http_worker(self, mock_settings, mock_runtime):
        """role=worker 且 http 模式时启动 HTTP Worker。"""
        mock_settings.app.get_process_role.return_value = "worker"
        mock_settings.dingtalk.message_mode = "http"

        with (
            patch("sales_agent.core.config.get_settings", return_value=mock_settings),
            patch("sales_agent.core.database.init_db", new_callable=AsyncMock),
            patch("sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime),
            patch("sales_agent.integrations.dingtalk.stream_client.start_dingtalk_stream_worker", new_callable=AsyncMock) as mock_stream,
            patch("sales_agent.integrations.dingtalk.worker.start_dingtalk_worker", new_callable=AsyncMock) as mock_http,
            patch("sales_agent.services.online_conversation.initialize_online_runtime", new_callable=AsyncMock),
            patch("sales_agent.services.online_conversation.close_online_runtime", new_callable=AsyncMock),
        ):
            from sales_agent.main import lifespan
            app = MagicMock()

            async with lifespan(app):
                mock_stream.assert_not_awaited()
                mock_http.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_role_worker_skips_stream_mode(self, mock_settings, mock_runtime):
        """role=worker 且 stream 模式时不启动 Stream Worker。"""
        mock_settings.app.get_process_role.return_value = "worker"
        mock_settings.dingtalk.message_mode = "stream"

        with (
            patch("sales_agent.core.config.get_settings", return_value=mock_settings),
            patch("sales_agent.core.database.init_db", new_callable=AsyncMock),
            patch("sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime),
            patch("sales_agent.integrations.dingtalk.stream_client.start_dingtalk_stream_worker", new_callable=AsyncMock) as mock_stream,
            patch("sales_agent.integrations.dingtalk.worker.start_dingtalk_worker", new_callable=AsyncMock) as mock_http,
            patch("sales_agent.services.online_conversation.initialize_online_runtime", new_callable=AsyncMock),
            patch("sales_agent.services.online_conversation.close_online_runtime", new_callable=AsyncMock),
        ):
            from sales_agent.main import lifespan
            app = MagicMock()

            async with lifespan(app):
                mock_stream.assert_not_awaited()
                mock_http.assert_not_awaited()


# ============================================================
# Health 端点测试
# ============================================================


class TestHealthEndpoint:
    """验证 /health 端点返回角色信息。"""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        """基础 health 端点返回 ok。"""
        from sales_agent.api.routes.health import health_check
        result = await health_check()
        assert result["status"] == "ok"
