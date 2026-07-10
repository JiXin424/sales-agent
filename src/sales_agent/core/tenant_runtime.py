"""TenantRuntime — 租户运行时抽象。

dedicated mode: TenantRuntime.from_current_env()
shared mode (未来): TenantRuntime.from_tenant_id(tenant_id)

Agent 执行层只依赖 TenantRuntime，不直接读环境变量或解析 API key。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sales_agent.core.secret_resolver import resolve_secret, key_fingerprint, SecretResolutionError
from sales_agent.llm import ModelProvider, OpenAICompatibleChat, OpenAICompatibleEmbedding

logger = logging.getLogger(__name__)


@dataclass
class TenantRuntime:
    """租户运行时 — 封装一个租户的全部运行配置。"""

    tenant_id: str
    tenant_name: str
    deployment_mode: str  # dedicated / shared
    model_provider: ModelProvider = field(default=None)
    # 密钥元信息（脱敏）
    api_key_ref: str = ""
    api_key_fingerprint: str = ""
    provider: str = ""
    base_url: str = ""
    base_url_host: str = ""
    chat_model: str = ""
    embedding_model: str = ""
    # 存储
    vector_collection: str = ""
    data_dir: str = ""
    log_dir: str = ""
    # 风险策略
    risk_policy: dict[str, Any] = field(default_factory=dict)
    # 禁用表达
    forbid_words: list[str] = field(default_factory=list)
    # 话术风格
    tone: str = "professional"
    default_script_versions: list[str] = field(
        default_factory=lambda: ["温和版", "推进版", "简短版"]
    )

    @classmethod
    def from_current_env(cls) -> "TenantRuntime":
        """从当前环境变量构建 TenantRuntime（dedicated mode）。"""
        from dotenv import load_dotenv

        # 加载 .env 文件
        load_dotenv()

        tenant_id = os.environ.get("TENANT_ID", "")
        tenant_name = os.environ.get("TENANT_NAME", "")
        deployment_mode = os.environ.get("DEPLOYMENT_MODE", "dedicated")
        provider = os.environ.get("MODEL_PROVIDER", "openai_compatible")
        api_key_ref = f"env:{os.environ.get('MODEL_API_KEY_REF', 'MODEL_API_KEY')}"
        base_url = os.environ.get("MODEL_BASE_URL", "")
        chat_model = os.environ.get("MODEL_CHAT_MODEL", "")
        embedding_model = os.environ.get("MODEL_EMBEDDING_MODEL", "")

        # 解析 API key
        try:
            api_key = resolve_secret(api_key_ref)
            fingerprint = key_fingerprint(api_key)
        except SecretResolutionError as e:
            logger.error("API key resolution failed: %s", e)
            api_key = ""
            fingerprint = "none"

        # 提取 base_url host
        base_url_host = ""
        if base_url:
            try:
                from urllib.parse import urlparse
                base_url_host = urlparse(base_url).hostname or ""
            except Exception:
                pass

        # 构建 ModelProvider（支持独立 embedding provider）
        model_provider = None

        # --- 优先使用 models.json（如果存在）---
        from sales_agent.core.model_registry import ModelRegistry

        _registry = ModelRegistry.load()
        if _registry is not None:
            _entry = _registry.get()  # default model
            if _entry and _entry.api_key and _entry.base_url:
                model_provider = ModelProvider(
                    chat=OpenAICompatibleChat(
                        api_key=_entry.api_key,
                        base_url=_entry.base_url,
                        model=_entry.chat_model,
                        temperature=_entry.temperature,
                        timeout_seconds=_entry.timeout_seconds,
                        max_retries=_entry.max_retries,
                    ),
                    embedding=OpenAICompatibleEmbedding(
                        api_key=_entry.embedding_api_key,
                        base_url=_entry.embedding_base_url,
                        model=_entry.embedding_model,
                        timeout_seconds=_entry.timeout_seconds,
                        max_retries=_entry.max_retries,
                    ),
                )
                # 用 models.json 的值覆盖 env var 读到的值
                chat_model = _entry.chat_model
                embedding_model = _entry.embedding_model
                base_url = _entry.base_url
                provider = _entry.provider
                logger.info(
                    "ModelProvider built from models.json: default_model=%s, chat=%s",
                    _registry.default_model, _entry.chat_model,
                )

        # --- 回退：env var 方式（models.json 不存在或加载失败）---
        if model_provider is None and api_key and base_url:
            # Embedding 可使用独立的 base_url 和 api_key
            embedding_base_url = os.environ.get("EMBEDDING_BASE_URL", "") or base_url
            embedding_api_key_env = os.environ.get("EMBEDDING_API_KEY_REF", "") or "EMBEDDING_API_KEY"

            embedding_key = api_key  # 默认与 chat 共用
            if os.environ.get("EMBEDDING_API_KEY"):
                try:
                    embedding_key = resolve_secret("env:EMBEDDING_API_KEY")
                except SecretResolutionError:
                    logger.warning("Embedding API key resolution failed, falling back to chat key")

            model_provider = ModelProvider(
                chat=OpenAICompatibleChat(
                    api_key=api_key,
                    base_url=base_url,
                    model=chat_model,
                ),
                embedding=OpenAICompatibleEmbedding(
                    api_key=embedding_key,
                    base_url=embedding_base_url,
                    model=embedding_model,
                ),
            )

        runtime = cls(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            deployment_mode=deployment_mode,
            model_provider=model_provider,
            api_key_ref=api_key_ref,
            api_key_fingerprint=fingerprint,
            provider=provider,
            base_url=base_url,
            base_url_host=base_url_host,
            chat_model=chat_model,
            embedding_model=embedding_model,
            vector_collection=os.environ.get("VECTOR_COLLECTION", ""),
            data_dir=os.environ.get("DATA_DIR", ""),
            log_dir=os.environ.get("LOG_DIR", ""),
        )

        logger.info(
            "TenantRuntime loaded: tenant_id=%s, mode=%s, provider=%s, "
            "base_url_host=%s, chat_model=%s, api_key_fingerprint=%s",
            runtime.tenant_id,
            runtime.deployment_mode,
            runtime.provider,
            runtime.base_url_host,
            runtime.chat_model,
            runtime.api_key_fingerprint,
        )

        return runtime

    def validate_startup(self) -> list[str]:
        """启动校验，返回错误列表。空列表表示校验通过。"""
        errors = []

        if not self.tenant_id:
            errors.append("TENANT_ID is required")

        if self.deployment_mode not in ("dedicated", "shared"):
            errors.append(f"Invalid DEPLOYMENT_MODE: {self.deployment_mode}")

        if not self.provider:
            errors.append("MODEL_PROVIDER is required")

        if not self.base_url:
            errors.append("MODEL_BASE_URL is required")

        if self.api_key_fingerprint == "none":
            errors.append("MODEL_API_KEY is required and non-empty")

        if not self.chat_model:
            errors.append("MODEL_CHAT_MODEL is required")

        if not self.embedding_model:
            errors.append("MODEL_EMBEDDING_MODEL is required")

        if not self.model_provider:
            errors.append("ModelProvider initialization failed (check API key and base URL)")

        return errors

    def check_tenant_match(self, request_tenant_id: str) -> bool:
        """dedicated mode 下检查请求租户是否匹配当前实例。"""
        if self.deployment_mode == "dedicated":
            return request_tenant_id == self.tenant_id
        # shared mode 不做此检查
        return True

    def get_debug_info(self) -> dict[str, Any]:
        """返回脱敏的 debug 信息。"""
        return {
            "provider": self.provider,
            "base_url_host": self.base_url_host,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model,
            "api_key_ref": self.api_key_ref,
            "api_key_fingerprint": self.api_key_fingerprint,
        }

    def get_log_info(self) -> dict[str, Any]:
        """返回脱敏的日志信息。"""
        return self.get_debug_info()

    # 允许展示的应用相关 env 变量前缀（白名单）。
    # 用白名单而非黑名单：黑名单会泄漏整个宿主环境（ANTHROPIC_AUTH_TOKEN、
    # SSH_*、SHELL、XDG_*、IDE token 等），其中匹配 *_TOKEN 的密钥会被
    # /instance/config 把完整值发到前端，构成密钥泄漏。白名单只暴露已知应用配置。
    _APP_ENV_PREFIXES: ClassVar[tuple[str, ...]] = (
        "DEPLOYMENT_", "TENANT_",
        "MODEL_", "EMBEDDING_",
        "VECTOR_", "DATA_DIR", "LOG_DIR",
        "DINGTALK_", "DINGTALK_",
        "NEO4J_", "ONTOLOGY_",
        "COACH_",
    )

    def get_all_env_vars(self) -> dict[str, str]:
        """返回所有应用相关的环境变量（白名单过滤，用于前端配置展示）。"""
        result: dict[str, str] = {}
        for key, value in sorted(os.environ.items()):
            if not value:
                continue
            if key.startswith(self._APP_ENV_PREFIXES):
                result[key] = value
        return result


# 全局单例
_runtime: TenantRuntime | None = None


def get_tenant_runtime() -> TenantRuntime:
    """获取全局 TenantRuntime 单例。"""
    global _runtime
    if _runtime is None:
        _runtime = TenantRuntime.from_current_env()
    return _runtime
