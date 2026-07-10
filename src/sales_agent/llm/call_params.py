"""LLM 调用参数（temperature/max_tokens）默认值加载器。

启动时 load_call_params() 读 config/llm_call_defaults.yaml 并校验，缓存进内存；
运行时 get_call_params(call_site) 零 IO 读取。仅开发者改，版本/回滚交 git。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_PARAMS: dict[str, "CallParams"] | None = None


@dataclass(frozen=True)
class CallParams:
    temperature: float
    max_tokens: int


def _coerce_and_validate(raw: dict) -> CallParams:
    if "temperature" not in raw or "max_tokens" not in raw:
        raise ValueError(f"调用点缺 temperature/max_tokens 字段: {raw}")
    try:
        temperature = float(raw["temperature"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"temperature 不是数字: {raw['temperature']!r}") from e
    try:
        max_tokens = int(raw["max_tokens"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"max_tokens 不是整数: {raw['max_tokens']!r}") from e
    if not (0.0 <= temperature <= 2.0):
        raise ValueError(f"temperature 超出 [0,2]: {temperature}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens 必须 > 0: {max_tokens}")
    return CallParams(temperature=temperature, max_tokens=max_tokens)


def load_call_params(path: str) -> None:
    """启动时调用：读 YAML、校验、缓存进模块级 _PARAMS。失败即抛。"""
    global _PARAMS
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"LLM 调用参数文件不存在: {path}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 调用参数文件顶层必须是 dict: {path}")
    params: dict[str, CallParams] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise ValueError(f"调用点 {key} 的值必须是 dict: {val!r}")
        params[key] = _coerce_and_validate(val)
    _PARAMS = params


def get_call_params(call_site: str) -> CallParams:
    """运行时读内存 dict。未加载抛 RuntimeError，未知 key 抛 KeyError。"""
    if _PARAMS is None:
        raise RuntimeError("LLM 调用参数未加载，请先在启动序列调 load_call_params()")
    if call_site not in _PARAMS:
        raise KeyError(f"未知 LLM 调用点 '{call_site}'，请检查 config/llm_call_defaults.yaml")
    return _PARAMS[call_site]
