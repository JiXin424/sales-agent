"""Phase 4：实时教练观察（coach_observe）。

首版**基于规则**：从当前消息 + 最近历史 + 任务类型推断销售场景提示，
判断是否值得生成引导。输出足够 coach_guidance 决策，不暴露内部维度。

输出字段：
- scene_hint：场景（见 SCENE_HINTS）。
- confidence：0..1。
- observed_signals：命中的信号列表。
- should_generate_guidance：bool。
- reason：不生成引导时的原因。

规则刻意保守：拿不准就 should_generate_guidance=False，让 coach_guidance
按分数带再决定，避免过度打扰。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 任务类型 → 场景提示的映射（粗粒度）
_TASK_SCENE: dict[str, str] = {
    "visit_preparation": "visit_preparation",
    "post_visit_review": "post_visit",
    "follow_up_planning": "next_step",
    "customer_context_summary": "pain_point_discovery",
    "deal_advancement": "closing_or_pricing",
    "objection_handling": "closing_or_pricing",
}

# 关键词 → (scene_hint, signal)
_KEYWORD_SIGNALS: list[tuple[str, str, str]] = [
    ("拜访", "visit_preparation", "mention_visit"),
    ("见客户", "visit_preparation", "mention_visit"),
    ("刚见完", "post_visit", "post_visit"),
    ("刚聊完", "post_visit", "post_visit"),
    ("太贵", "closing_or_pricing", "pricing_objection"),
    ("报价", "closing_or_pricing", "pricing"),
    ("签", "closing_or_pricing", "mention_signing"),
    ("预算", "pain_point_discovery", "mention_budget"),
    ("痛点", "pain_point_discovery", "mention_pain"),
    ("需求", "pain_point_discovery", "mention_need"),
    ("演示", "product_demo", "mention_demo"),
    ("不回", "dormant_customer", "dormant"),
    ("没回", "dormant_customer", "dormant"),
    ("焦虑", "frustration", "frustration"),
    ("没信心", "frustration", "frustration"),
    ("放弃", "frustration", "frustration"),
    ("下一步", "next_step", "mention_next_step"),
    ("跟", "next_step", "mention_followup"),
]


@dataclass
class ObserveResult:
    scene_hint: str | None = None
    confidence: float = 0.0
    observed_signals: list[str] = field(default_factory=list)
    should_generate_guidance: bool = False
    reason: str = ""


def observe(
    *,
    tenant_id: str,
    agent_id: str,
    user_id: str,
    message: str,
    recent_history: list[dict[str, str]] | None = None,
    task_type: str | None = None,
    conversation_id: str | None = None,
) -> ObserveResult:
    """规则化的场景观察。纯函数、无 I/O。"""
    if not message or not message.strip():
        return ObserveResult(reason="empty_message")

    text = message.strip()
    signals: list[str] = []
    scene_hits: dict[str, int] = {}

    # 1. 关键词命中
    for kw, scene, signal in _KEYWORD_SIGNALS:
        if kw in text:
            signals.append(signal)
            scene_hits[scene] = scene_hits.get(scene, 0) + 1

    # 2. 任务类型映射
    scene_hint: str | None = None
    if task_type and task_type in _TASK_SCENE:
        scene_hint = _TASK_SCENE[task_type]
        scene_hits[scene_hint] = scene_hits.get(scene_hint, 0) + 2  # 任务类型权重更高

    # 3. 历史信号：最近是否在谈同一场景（简单聚合）
    if recent_history:
        hist_text = " ".join(m.get("content", "") for m in recent_history[-4:])
        for kw, scene, signal in _KEYWORD_SIGNALS:
            if kw in hist_text and signal not in signals:
                signals.append(signal)
                scene_hits[scene] = scene_hits.get(scene, 0) + 1

    # 选择命中权重最高的场景
    if scene_hits:
        scene_hint = max(scene_hits.items(), key=lambda kv: kv[1])[0]

    if not scene_hint:
        return ObserveResult(
            scene_hint=None, confidence=0.0, observed_signals=signals,
            should_generate_guidance=False, reason="no_sales_scene_detected",
        )

    confidence = min(0.95, 0.4 + 0.15 * scene_hits.get(scene_hint, 0))
    # 只有命中足够信号才提议生成引导；coach_guidance 还会按分数带/抑制规则再判
    should = confidence >= 0.55
    return ObserveResult(
        scene_hint=scene_hint, confidence=round(confidence, 2),
        observed_signals=list(dict.fromkeys(signals)),  # 去重保序
        should_generate_guidance=should,
        reason="" if should else "low_confidence",
    )
