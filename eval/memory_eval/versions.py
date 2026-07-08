"""Collect every version a memory-eval report must record (Spec 4 §2.5, §7)."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionBundle:
    model_version: str
    prompt_version: str
    code_version: str
    dataset_version: str
    knowledge_version: str
    memory_schema_version: str
    generator_version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _safe_get_settings():
    try:
        from sales_agent.core.config import get_settings  # local import: heavy
        return get_settings()
    except Exception:  # pragma: no cover - best-effort
        logger.debug("get_settings unavailable; defaulting model version", exc_info=True)
        return None


def _code_version() -> str:
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("sales-agent")
        except PackageNotFoundError:
            return "0.1.0-dev"
    except Exception:  # pragma: no cover
        return "unknown"


def _model_version() -> str:
    settings = _safe_get_settings()
    if settings is None:
        return "unset"
    model = getattr(settings, "model", None)
    name = getattr(model, "chat_model", None) or getattr(model, "default_model", None)
    return str(name or "unset")


def _prompt_version() -> str:
    settings = _safe_get_settings()
    registry = getattr(settings, "prompt_registry", None) if settings else None
    return getattr(registry, "version", "db-managed") if registry else "db-managed"


def collect_version_bundle(
    *,
    dataset_version: Optional[str] = None,
    knowledge_version: Optional[str] = None,
    memory_schema_version: Optional[str] = None,
    generator_version: str = "memory_eval_v1",
) -> VersionBundle:
    """Build a VersionBundle, defaulting missing inputs to explicit strings.

    Per §2.6, missing inputs are reported explicitly — never silently None.
    """
    return VersionBundle(
        model_version=_model_version(),
        prompt_version=_prompt_version(),
        code_version=_code_version(),
        dataset_version=dataset_version or "unset",
        knowledge_version=knowledge_version or "unset",
        memory_schema_version=memory_schema_version or "unset",
        generator_version=generator_version,
    )


__all__ = ["VersionBundle", "collect_version_bundle"]
