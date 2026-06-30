"""Model registry — 从 models.json 加载多模型配置。

设计原则：简单。一个 dataclass 存已解析的模型配置，一个类方法加载 JSON。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认的 models.json 路径（相对于本文件：src/sales_agent/core/model_registry.py → 项目根目录）
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "models.json"


@dataclass
class ModelEntry:
    """一个已解析的模型配置（api_key 已从环境变量解析为实际值）。"""

    name: str
    provider: str
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    temperature: float = 0.3
    timeout_seconds: int = 30
    max_retries: int = 2


@dataclass
class ModelRegistry:
    """多模型注册表，从 models.json 加载。"""

    default_model: str
    entries: dict[str, ModelEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> ModelRegistry | None:
        """加载 models.json。

        Returns:
            ModelRegistry 如果文件存在且有效，None 表示回退到旧 env var 方式。
        """
        if config_path is None:
            config_path = _DEFAULT_CONFIG_PATH
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            logger.info("models.json not found at %s, falling back to legacy env var config", config_path)
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse models.json: %s, falling back to legacy config", e)
            return None

        default_model = raw.get("default_model", "")
        models_raw = raw.get("models", {})
        if not models_raw:
            logger.warning("models.json has no models defined")
            return None

        entries: dict[str, ModelEntry] = {}
        for name, cfg in models_raw.items():
            api_key_env = cfg.get("api_key_env", "")
            api_key = os.environ.get(api_key_env, "") if api_key_env else ""

            embedding_api_key_env = cfg.get("embedding_api_key_env", "") or api_key_env
            embedding_api_key = (
                os.environ.get(embedding_api_key_env, "") if embedding_api_key_env else api_key
            )

            embedding_base_url = cfg.get("embedding_base_url", "") or cfg.get("base_url", "")

            entries[name] = ModelEntry(
                name=name,
                provider=cfg.get("provider", ""),
                base_url=cfg.get("base_url", ""),
                api_key=api_key,
                chat_model=name,  # chat_model 默认等于模型名
                embedding_model=cfg.get("embedding_model", "text-embedding-v3"),
                embedding_base_url=embedding_base_url,
                embedding_api_key=embedding_api_key,
                temperature=float(cfg.get("temperature", 0.3)),
                timeout_seconds=int(cfg.get("timeout_seconds", 30)),
                max_retries=int(cfg.get("max_retries", 2)),
            )

            if not api_key:
                logger.warning(
                    "Model %r: env var %r not set or empty, API calls will fail",
                    name, api_key_env,
                )

        logger.info(
            "Loaded %d models from models.json, default=%s",
            len(entries), default_model,
        )
        return cls(default_model=default_model, entries=entries)

    def get(self, model_name: str | None = None) -> ModelEntry | None:
        """获取模型配置。model_name 为 None 时返回默认模型。"""
        if model_name is None:
            model_name = self.default_model
        return self.entries.get(model_name)

    def list_models(self) -> list[str]:
        """返回所有模型名列表。"""
        return list(self.entries.keys())
