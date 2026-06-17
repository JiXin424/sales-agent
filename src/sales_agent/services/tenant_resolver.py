"""Tenant configuration resolver.

Dedicated mode: 从 TenantRuntime 获取租户配置和模型 provider。
Shared mode (未来): 从数据库加载租户配置，通过 SecretResolver 解析 key。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import TenantNotFoundError, TenantDisabledError
from sales_agent.core.secret_resolver import resolve_secret, key_fingerprint
from sales_agent.core.tenant_runtime import get_tenant_runtime, TenantRuntime
from sales_agent.llm import ModelProvider, OpenAICompatibleChat, OpenAICompatibleEmbedding
from sales_agent.models.tenant import Tenant

logger = logging.getLogger(__name__)


class TenantResolver:
    """Resolve tenant configuration and construct per-tenant model providers."""

    # 类级别缓存：同一 tenant_id 复用 ModelProvider
    _provider_cache: dict[str, ModelProvider] = {}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def resolve(self, tenant_id: str) -> dict[str, Any]:
        """Load tenant from DB and return a resolved info dict.

        In dedicated mode, also validates tenant_id matches current instance.
        """
        runtime = get_tenant_runtime()

        # dedicated mode 校验
        if not runtime.check_tenant_match(tenant_id):
            from sales_agent.core.exceptions import TenantMismatchError
            raise TenantMismatchError(tenant_id, runtime.tenant_id)

        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await self.db.execute(stmt)
        tenant: Tenant | None = result.scalar_one_or_none()

        if tenant is None:
            raise TenantNotFoundError(tenant_id)

        if tenant.status != "active":
            raise TenantDisabledError(tenant_id)

        try:
            config: dict[str, Any] = json.loads(tenant.config_json)
        except json.JSONDecodeError:
            logger.warning("Invalid config_json for tenant %s", tenant_id)
            config = {}

        return {
            "tenant_id": tenant.id,
            "name": tenant.name,
            "status": tenant.status,
            "config": config,
        }

    def get_model_provider(self, tenant_config: dict[str, Any]) -> ModelProvider:
        """Create a ModelProvider for the given tenant.

        Dedicated mode: 优先使用 TenantRuntime 中的 provider。
        Shared mode: 使用类级别缓存复用 ModelProvider。
        """
        runtime = get_tenant_runtime()

        # dedicated mode 直接使用运行时 provider
        if runtime.deployment_mode == "dedicated" and runtime.model_provider is not None:
            return runtime.model_provider

        # shared mode: 检查缓存
        tenant_id = tenant_config.get("tenant_id", "")
        if tenant_id and tenant_id in self._provider_cache:
            return self._provider_cache[tenant_id]

        # shared mode / fallback: 从配置构建
        defaults = get_settings().model
        tenant_model_cfg: dict[str, Any] = tenant_config.get("config", {}).get("model", {})

        # 解析 api_key_ref (v0 新格式) 或 api_key_env (兼容旧格式)
        api_key_ref = tenant_model_cfg.get("api_key_ref", "")
        if api_key_ref:
            try:
                api_key = resolve_secret(api_key_ref)
            except Exception as e:
                logger.error("API key resolution failed for ref %s: %s", api_key_ref, e)
                api_key = ""
        else:
            # 兼容旧 api_key_env 格式
            api_key_env = tenant_model_cfg.get("api_key_env", defaults.api_key_env)
            import os
            api_key = os.environ.get(api_key_env, "")

        base_url = tenant_model_cfg.get("base_url", defaults.base_url)
        chat_model_name = tenant_model_cfg.get("chat_model", defaults.chat_model)
        embedding_model_name = tenant_model_cfg.get("embedding_model", defaults.embedding_model)
        temperature = float(tenant_model_cfg.get("temperature", defaults.temperature))
        timeout_seconds = int(tenant_model_cfg.get("timeout_seconds", defaults.timeout_seconds))
        max_retries = int(tenant_model_cfg.get("max_retries", defaults.max_retries))

        logger.info(
            "Building ModelProvider: provider=%s, base_url=%s, chat=%s, "
            "api_key_fingerprint=%s",
            tenant_model_cfg.get("provider", defaults.provider),
            base_url,
            chat_model_name,
            key_fingerprint(api_key) if api_key else "none",
        )

        chat = OpenAICompatibleChat(
            api_key=api_key,
            base_url=base_url,
            model=chat_model_name,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

        embedding = OpenAICompatibleEmbedding(
            api_key=api_key,
            base_url=base_url,
            model=embedding_model_name,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

        provider = ModelProvider(chat=chat, embedding=embedding)

        # 缓存
        if tenant_id:
            self._provider_cache[tenant_id] = provider

        return provider
