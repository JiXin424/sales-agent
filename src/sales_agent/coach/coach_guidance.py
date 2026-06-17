"""Phase 4：实时教练引导（coach_guidance）。

输入：场景提示、六维当前分数、弱维度、任务类型、当前消息、当日已用引导数、
      依赖规则。输出引导强度（specific / directional / suppressed）+ 引导文本。

分数带（spec）：
- score < 40：specific（具体方法 + 示例话术）。
- 40 <= score <= 70：directional（方向性提醒）。
- score > 70：suppressed（默认抑制）。

依赖规则示例：
- needs_discovery 弱 且 customer_identification 也弱 → 先加强客户识别。

抑制规则：
- 帮助/重置命令、教练报告请求、纯知识问答（与销售行动无关）、
  当日引导次数超限、高分无明显风险、回复已很长或引导重复。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sales_agent.coach.constants import (
    DIMENSION_LABELS,
    GUIDANCE_DIRECTIONAL_MAX_SCORE,
    GUIDANCE_SPECIFIC_MAX_SCORE,
    GUIDANCE_LEVEL_DIRECTIONAL,
    GUIDANCE_LEVEL_SPECIFIC,
    GUIDANCE_LEVEL_SUPPRESSED,
)
from sales_agent.services.task_router import KNOWLEDGE_QA

# 各场景的"特定方法"模板（specific 用），按场景给一句可落地的建议。
_SPECIFIC_TEMPLATES: dict[str, str] = {
    "visit_preparation": "见客户前先把对方的角色、采购决策链、本次目标补全，再决定主推哪一类价值点。",
    "post_visit": "复盘时先复述客户原话里的需求，再给出下一步明确动作和时间点。",
    "pain_point_discovery": "用 SPIN 式追问：先问现状，再问困难与影响，最后问理想状态，避免直接推销。",
    "product_demo": "演示时先确认客户最在意的指标，把功能对照到那个指标上讲。",
    "next_step": "给客户一个具体的下一步（时间+动作+负责人），而不是让对方再看看。",
    "closing_or_pricing": "谈价前先确认价值已对齐；用算账式表达（省多少/赚多少）代替直接打折。",
    "dormant_customer": "沉睡客户先给一条轻价值信息，再问一个低门槛问题，避免直接催。",
    "frustration": "先共情，再把情绪拆成可做的最小一步。",
}

_DIRECTIONAL_TEMPLATES: dict[str, str] = {
    "visit_preparation": "记得把客户角色和目标想清楚再开场。",
    "post_visit": "复盘时落到一个明确的下一步。",
    "pain_point_discovery": "多追问一层，别急着给方案。",
    "product_demo": "演示贴着客户最在意的指标讲。",
    "next_step": "下一步要具体到时间与动作。",
    "closing_or_pricing": "先对齐价值，再谈价。",
    "dormant_customer": "沉睡客户用轻价值+低门槛问题唤醒。",
    "frustration": "先稳住情绪，再拆最小一步。",
}


@dataclass
class GuidanceResult:
    guidance_level: str = GUIDANCE_LEVEL_SUPPRESSED
    guidance_text: str = ""
    dimension_focus: str | None = None
    suppressed_reason: str = ""
    confidence: float = 0.0


def _weakest_dimension(scores: dict[str, int]) -> str | None:
    if not scores:
        return None
    return min(scores.items(), key=lambda kv: kv[1])[0]


def generate_guidance(
    *,
    scene_hint: str | None,
    scores: dict[str, int],
    weak_dimensions: list[str] | None = None,
    task_type: str | None = None,
    message: str,
    daily_guidance_used: int = 0,
    daily_guidance_limit: int = 3,
    observe_confidence: float = 0.0,
) -> GuidanceResult:
    """生成实时引导决策。

    ``scores``: {dimension: score}（只含弱维度或全部，由调用方决定）。
    """
    text = (message or "").strip()

    # 1. 硬抑制：命令、报告、纯知识问答
    if task_type in ("coach_report",):
        return GuidanceResult(suppressed_reason="coach_report_request")
    if _is_command(text):
        return GuidanceResult(suppressed_reason="help_or_reset_command")
    if task_type == KNOWLEDGE_QA and not _is_sales_action(text):
        return GuidanceResult(suppressed_reason="pure_knowledge_qa")

    # 2. 当日上限
    if daily_guidance_used >= daily_guidance_limit:
        return GuidanceResult(suppressed_reason="daily_limit_exceeded")

    # 3. 无场景或置信度低 → 抑制
    if not scene_hint or observe_confidence < 0.5:
        return GuidanceResult(suppressed_reason="no_clear_scene")

    # 4. 选择聚焦维度（弱维度优先；依赖规则）
    focus = _resolve_focus(scene_hint, scores, weak_dimensions)
    focus_score = scores.get(focus) if focus else None

    # 5. 分数带决策
    if focus_score is not None and focus_score <= GUIDANCE_SPECIFIC_MAX_SCORE:
        tmpl = _SPECIFIC_TEMPLATES.get(scene_hint, "")
        body = tmpl
        if focus:
            body = f"在「{DIMENSION_LABELS.get(focus, focus)}」上可以更稳：{tmpl}"
        return GuidanceResult(
            guidance_level=GUIDANCE_LEVEL_SPECIFIC,
            guidance_text=body,
            dimension_focus=focus,
            confidence=min(0.9, observe_confidence + 0.1),
        )
    if focus_score is not None and focus_score <= GUIDANCE_DIRECTIONAL_MAX_SCORE:
        body = _DIRECTIONAL_TEMPLATES.get(scene_hint, "")
        return GuidanceResult(
            guidance_level=GUIDANCE_LEVEL_DIRECTIONAL,
            guidance_text=body,
            dimension_focus=focus,
            confidence=observe_confidence,
        )
    # 高分：默认抑制
    return GuidanceResult(
        guidance_level=GUIDANCE_LEVEL_SUPPRESSED,
        dimension_focus=focus,
        suppressed_reason="high_score_no_obvious_need",
    )


def _resolve_focus(
    scene_hint: str,
    scores: dict[str, int],
    weak_dimensions: list[str] | None,
) -> str | None:
    """聚焦维度：依赖规则 + 弱维度。

    依赖示例：needs_discovery 弱且 customer_identification 也弱 → 先加强客户识别。
    """
    weak = weak_dimensions or [k for k, v in scores.items() if v <= GUIDANCE_DIRECTIONAL_MAX_SCORE]
    weak_set = set(weak)

    # 依赖规则
    if "needs_discovery" in weak_set and "customer_identification" in weak_set:
        return "customer_identification"

    if weak:
        return _weakest_dimension({k: scores.get(k, 50) for k in weak})
    return None


_COMMAND_TOKENS = {"帮助", "help", "？", "?", "新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}
_SALES_ACTION_KEYWORDS = ("客户", "拜访", "签", "报价", "预算", "跟进", "需求", "异议", "话术", "复盘", "成交", "跟")


def _is_command(text: str) -> bool:
    t = text.strip()
    return t in _COMMAND_TOKENS or t.lower() in _COMMAND_TOKENS


def _is_sales_action(text: str) -> bool:
    return any(k in text for k in _SALES_ACTION_KEYWORDS)
