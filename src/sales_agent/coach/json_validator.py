"""每日评估 JSON 的严格校验与归一化（纯函数）。

职责：
- 把 LLM 返回的 JSON 归一化为可信的评估 payload。
- 强制规则：
  * 六个维度必须齐全，否则整次评估 failed。
  * delta 必须是 [-3, +3] 内的整数；越界/非整数 → 整次评估 failed。
  * 非零 delta 必须带 reason / evidence_quotes / confidence；
    证据不足时把**该维度** delta 归零（而非整次失败）。
  * data_sufficiency=insufficient → 整次评估 skipped。
  * 冰山块按允许类型过滤，surface≤5 / deep≤4。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sales_agent.coach.constants import (
    DATA_SUFFICIENCY_INSUFFICIENT,
    DATA_SUFFICIENCY_SUFFICIENT,
    DATA_SUFFICIENCY_VALUES,
    DEFAULT_EVIDENCE_QUOTE_MAX_CHARS,
    DELTA_MAX,
    DELTA_MIN,
    ICEBERG_DEEP_TYPES,
    ICEBERG_SEVERITY_VALUES,
    ICEBERG_SURFACE_TYPES,
    MAX_DEEP_BLOCKS,
    MAX_SURFACE_BLOCKS,
    clamp_score,
    is_valid_delta,
)

EVAL_SUCCESS = "success"
EVAL_SKIPPED = "skipped"
EVAL_FAILED = "failed"


class InvalidDeltaError(Exception):
    """delta 越界或非整数 —— 整次评估失败。"""


@dataclass
class ValidationResult:
    """校验结果。status ∈ success / skipped / failed。"""

    status: str
    payload: dict[str, Any] | None = None
    reason: str = ""


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def normalize_dimension(entry: Any, evidence_max_chars: int = DEFAULT_EVIDENCE_QUOTE_MAX_CHARS) -> dict[str, Any]:
    """归一化单个维度。

    - entry 非对象 → 视为 delta=0。
    - delta 越界/非整数 → 抛 InvalidDeltaError（由上层判定整次失败）。
    - 非零 delta 缺证据 → 该维度 delta 归零。
    """
    if not isinstance(entry, dict):
        return {
            "delta": 0,
            "reason": "",
            "evidence_quotes": [],
            "source_conversation_ids": [],
            "confidence": 0.0,
        }

    raw_delta = entry.get("delta", 0)
    if not is_valid_delta(raw_delta):
        raise InvalidDeltaError(
            f"delta must be an integer in [{DELTA_MIN}, {DELTA_MAX}], got {raw_delta!r}"
        )
    delta = int(raw_delta)

    reason = entry.get("reason", "")
    raw_quotes = entry.get("evidence_quotes", [])
    confidence = entry.get("confidence")

    if delta != 0:
        has_reason = isinstance(reason, str) and reason.strip()
        has_quotes = isinstance(raw_quotes, list) and len(raw_quotes) > 0
        has_confidence = (
            isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        )
        if not (has_reason and has_quotes and has_confidence):
            # 证据不足：该维度归零，继续评估其它维度
            delta = 0
            reason = ""
            quotes: list[str] = []
        else:
            quotes = [str(q)[:evidence_max_chars] for q in raw_quotes if q]
    else:
        quotes = []

    raw_conv_ids = entry.get("source_conversation_ids", [])
    conv_ids = [str(c) for c in raw_conv_ids if c] if isinstance(raw_conv_ids, list) else []

    return {
        "delta": delta,
        "reason": str(reason).strip() if reason else "",
        "evidence_quotes": quotes,
        "source_conversation_ids": conv_ids,
        "confidence": _coerce_confidence(confidence) if delta != 0 else 0.0,
    }


def _normalize_blocks(
    blocks: Any, allowed_types: set[str], cap: int
) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        return []
    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype not in allowed_types:
            continue
        severity = b.get("severity", "medium")
        if severity not in ICEBERG_SEVERITY_VALUES:
            severity = "medium"
        raw_quotes = b.get("evidence_quotes", [])
        quotes = [str(q)[:DEFAULT_EVIDENCE_QUOTE_MAX_CHARS] for q in raw_quotes if q] if isinstance(raw_quotes, list) else []
        raw_conv_ids = b.get("source_conversation_ids", [])
        conv_ids = [str(c) for c in raw_conv_ids if c] if isinstance(raw_conv_ids, list) else []
        out.append({
            "type": btype,
            "severity": severity,
            "description": str(b.get("description", "")).strip(),
            "evidence_quotes": quotes,
            "source_conversation_ids": conv_ids,
        })
        if len(out) >= cap:
            break
    return out


def normalize_iceberg(iceberg: Any) -> dict[str, list[dict[str, Any]]]:
    """归一化冰山：过滤类型、封顶数量。"""
    if not isinstance(iceberg, dict):
        return {"surface_blocks": [], "deep_blocks": []}
    return {
        "surface_blocks": _normalize_blocks(
            iceberg.get("surface_blocks", []), ICEBERG_SURFACE_TYPES, MAX_SURFACE_BLOCKS
        ),
        "deep_blocks": _normalize_blocks(
            iceberg.get("deep_blocks", []), ICEBERG_DEEP_TYPES, MAX_DEEP_BLOCKS
        ),
    }


def validate_evaluation_payload(
    data: Any,
    *,
    evidence_max_chars: int = DEFAULT_EVIDENCE_QUOTE_MAX_CHARS,
) -> ValidationResult:
    """校验整份评估 payload。"""
    if not isinstance(data, dict):
        return ValidationResult(EVAL_FAILED, reason="评估结果不是合法 JSON 对象")

    # 数据充分性
    ds = data.get("data_sufficiency", DATA_SUFFICIENCY_SUFFICIENT)
    if ds not in DATA_SUFFICIENCY_VALUES:
        return ValidationResult(
            EVAL_FAILED, reason=f"非法的 data_sufficiency: {ds!r}"
        )
    if ds == DATA_SUFFICIENCY_INSUFFICIENT:
        return ValidationResult(
            EVAL_SKIPPED, reason="LLM 判定当日数据不足，跳过评估"
        )

    # 六维必须齐全
    dims = data.get("dimensions")
    if not isinstance(dims, dict):
        return ValidationResult(EVAL_FAILED, reason="dimensions 必须是对象")

    from sales_agent.coach.constants import DIMENSION_KEYS

    missing = [k for k in DIMENSION_KEYS if k not in dims]
    if missing:
        return ValidationResult(
            EVAL_FAILED, reason=f"缺少维度: {', '.join(missing)}"
        )

    # 归一化每个维度（delta 越界 → 整次失败）
    normalized_dims: dict[str, dict[str, Any]] = {}
    try:
        for key in DIMENSION_KEYS:
            normalized_dims[key] = normalize_dimension(dims[key], evidence_max_chars)
    except InvalidDeltaError as e:
        return ValidationResult(EVAL_FAILED, reason=str(e))

    iceberg = normalize_iceberg(data.get("iceberg"))

    payload = {
        "data_sufficiency": DATA_SUFFICIENCY_SUFFICIENT,
        "summary": str(data.get("summary", "")).strip(),
        "dimensions": normalized_dims,
        "iceberg": iceberg,
        "next_growth_suggestion": str(data.get("next_growth_suggestion", "")).strip(),
        "points": data.get("points") if isinstance(data.get("points"), dict) else None,
    }
    return ValidationResult(EVAL_SUCCESS, payload=payload)


def build_score_deltas(payload: dict[str, Any]) -> dict[str, int]:
    """从校验后的 payload 抽取 {dimension: delta}。"""
    dims = payload.get("dimensions", {})
    return {k: int(d.get("delta", 0)) for k, d in dims.items()}
