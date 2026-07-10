"""Prompt 模板加载器 — YAML → 内存缓存，运行时零 IO。
启动时 load_prompts() 读 config/prompts.yaml；运行时 get_prompt(category, key) 零 IO。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_PROMPTS: dict[tuple[str, str], "PromptTemplate"] | None = None


@dataclass(frozen=True)
class PromptTemplate:
    template: str
    placeholders: tuple[str, ...]
    description: str


def _coerce_and_validate(raw: dict, category: str, key: str) -> PromptTemplate:
    if not isinstance(raw, dict):
        raise ValueError(f"prompt {category}.{key} 的值必须是 dict: {raw!r}")
    template = raw.get("template", "")
    if not template or not isinstance(template, str):
        raise ValueError(f"prompt {category}.{key} 的 template 不能为空")
    placeholders = tuple(raw.get("placeholders", []) or [])
    description = raw.get("description", "") or ""
    return PromptTemplate(template=template, placeholders=placeholders, description=description)


def load_prompts(path: str) -> None:
    """启动时调用：读 YAML、校验、缓存进 _PROMPTS。失败即崩。"""
    global _PROMPTS
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Prompt 配置文件不存在: {path}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Prompt 配置文件顶层必须是 dict: {path}")
    raw = raw.get("prompts", raw)  # 有 "prompts" 段取其内容，否则整个文件即 prompt（兼容）
    prompts: dict[tuple[str, str], PromptTemplate] = {}
    for category, cat_dict in raw.items():
        if not isinstance(cat_dict, dict):
            raise ValueError(f"category {category} 的值必须是 dict")
        for key, val in cat_dict.items():
            prompts[(category, key)] = _coerce_and_validate(val, category, key)
    _PROMPTS = prompts


def get_prompt(category: str, key: str) -> PromptTemplate:
    """运行时读内存 dict。未知 (category,key) 抛 KeyError。"""
    if _PROMPTS is None:
        raise RuntimeError("Prompt 未加载，请先在启动序列调 load_prompts()")
    k = (category, key)
    if k not in _PROMPTS:
        raise KeyError(f"未知 prompt '{category}.{key}'，请检查 config/prompts.yaml")
    return _PROMPTS[k]
