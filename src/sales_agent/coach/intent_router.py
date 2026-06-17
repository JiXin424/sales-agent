"""CoachIntentRouter —— 教练报告意图识别。

在 ChatPipeline 中、Agent 解析之后、正常任务路由之前运行。
匹配到报告意图时直接渲染报告返回，不走 RAG / 生成 / 风控 / 实时引导。

关键约束：**不得"偷走"既有的 conversation_scoring 任务**。
因此裸 "评分" 这类关键词必须做反遮蔽（anti-shadowing）：只有当消息是
"报告式"短句、且不含对话评分语境词时才命中 scores。
"""

from __future__ import annotations

from sales_agent.coach.constants import (
    REPORT_FULL,
    REPORT_ICEBERG,
    REPORT_LEVEL,
    REPORT_MILESTONES,
    REPORT_REWARDS,
    REPORT_SCORES,
)

# 触发表：按特异性从高到低，先匹配最具体的。
# (report_type, keywords)
_TRIGGERS: list[tuple[str, list[str]]] = [
    (REPORT_FULL, ["教练报告", "完整报告", "我的教练报告"]),
    (REPORT_ICEBERG, ["冰山", "我卡在哪", "卡在哪", "深层问题", "哪里需要提升", "我卡在哪里"]),
    (REPORT_LEVEL, ["我的等级", "我的段位", "等级", "段位"]),
    (REPORT_MILESTONES, ["里程碑"]),
    (REPORT_REWARDS, ["奖励", "徽章", "红包"]),
    (REPORT_SCORES, ["我的评分", "我的能力", "能力报告"]),
]

# 裸 "评分" 单独处理：这些上下文词出现时说明用户想要 conversation_scoring
_SCORING_CONTEXT_WORDS = ("对话", "销售", "通话", "沟通", "给", "打分", "标准", "帮我", "这段")

# 报告式语境词：出现这些词时，裸 "评分" 才视为 scores 报告
_REPORT_LIKE_WORDS = ("我的", "能力", "报告")

# help / reset 短语：绝不视为教练报告
_HELP_RESET = {"帮助", "help", "？", "?", "新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}


def match_coach_intent(message: str) -> str | None:
    """识别教练报告意图，返回报告类型或 None。

    纯函数、无副作用、无 I/O。
    """
    if not message:
        return None
    text = message.strip()
    if not text:
        return None
    if text in _HELP_RESET or text.lower() in _HELP_RESET:
        return None

    # 1. 按特异性顺序匹配显式关键词
    for report_type, keywords in _TRIGGERS:
        for kw in keywords:
            if kw in text:
                return report_type

    # 2. 裸 "评分" —— 仅当报告式且无对话评分语境时才命中
    if "评分" in text:
        if _looks_report_like(text):
            return REPORT_SCORES

    return None


def _looks_report_like(text: str) -> bool:
    """裸 '评分' 是否更像报告请求而非对话评分请求。"""
    if any(w in text for w in _SCORING_CONTEXT_WORDS):
        return False
    # 出现报告式语境词
    if any(w in text for w in _REPORT_LIKE_WORDS):
        return True
    # 短句且几乎只有 "评分" 本身
    if len(text) <= 4:
        return True
    return False
