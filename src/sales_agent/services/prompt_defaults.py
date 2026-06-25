"""内置 Prompt 注册表（single source of truth）。

集中所有随代码发布的默认 prompt 常量，作为 ``PromptRegistry`` 三级回退的最后一层
（Agent 绑定 → tenant active 版本 → 本表）。每项是 ``BuiltinPrompt``，含 category、
key、模板正文、运行时必须注入的占位符、描述。

新增 prompt 层级时：
1. 把常量放到 ``prompts/`` 下对应模块（纯字符串，无 service 依赖，避免循环导入）；
2. 在此注册表的 ``BUILTIN_PROMPTS`` 里追加一行。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinPrompt:
    """一个内置 prompt 的元信息与模板。"""

    category: str  # task | system | router | risk | coach
    key: str  # 该类别下的具体标识
    template: str  # 模板正文（含 {placeholder}）
    required_placeholders: tuple[str, ...]  # 运行时 .format() 必须注入的占位符
    description: str = ""

    @property
    def placeholders_json(self) -> str:
        """占位符列表的 JSON 字符串表示（用于存入 PromptVersion.required_placeholders_json）。"""
        import json

        return json.dumps(list(self.required_placeholders), ensure_ascii=False)


# task 类校验时必须包含的占位符（executor 会对所有 task 统一传入
# message/context_block/retrieval_block/retrieval_content，但模板按需使用，
# 因此只强制要求 {message}，其余为可选）。
_TASK_PLACEHOLDERS = ("message",)


def _task_entries() -> list[BuiltinPrompt]:
    """构造 12 个 task 类内置 prompt。延迟 import 避免模块加载顺序问题。"""
    from sales_agent.prompts import (
        conversation_review,
        customer_context_summary,
        deal_advancement,
        emotional_support,
        follow_up_planning,
        general_coaching,
        knowledge_qa,
        objection_handling,
        post_visit_review,
        script_generation,
        visit_preparation,
        conversation_scoring,
    )

    return [
        BuiltinPrompt("task", "emotional_support", emotional_support.EMOTIONAL_SUPPORT_PROMPT, _TASK_PLACEHOLDERS, "情绪支持"),
        BuiltinPrompt("task", "knowledge_qa", knowledge_qa.KNOWLEDGE_QA_PROMPT, _TASK_PLACEHOLDERS, "知识问答"),
        BuiltinPrompt("task", "script_generation", script_generation.SCRIPT_GENERATION_PROMPT, _TASK_PLACEHOLDERS, "话术生成"),
        BuiltinPrompt("task", "objection_handling", objection_handling.OBJECTION_HANDLING_PROMPT, _TASK_PLACEHOLDERS, "异议处理"),
        BuiltinPrompt("task", "conversation_review", conversation_review.CONVERSATION_REVIEW_PROMPT, _TASK_PLACEHOLDERS, "销售复盘"),
        BuiltinPrompt("task", "general_sales_coaching", general_coaching.GENERAL_COACHING_PROMPT, _TASK_PLACEHOLDERS, "通用教练"),
        BuiltinPrompt("task", "visit_preparation", visit_preparation.VISIT_PREPARATION_PROMPT, _TASK_PLACEHOLDERS, "访前作战卡"),
        BuiltinPrompt("task", "follow_up_planning", follow_up_planning.FOLLOW_UP_PLANNING_PROMPT, _TASK_PLACEHOLDERS, "跟进计划"),
        BuiltinPrompt("task", "customer_context_summary", customer_context_summary.CUSTOMER_CONTEXT_SUMMARY_PROMPT, _TASK_PLACEHOLDERS, "客户上下文整理"),
        BuiltinPrompt("task", "deal_advancement", deal_advancement.DEAL_ADVANCEMENT_PROMPT, _TASK_PLACEHOLDERS, "成交推进"),
        BuiltinPrompt("task", "conversation_scoring", conversation_scoring.CONVERSATION_SCORING_PROMPT, _TASK_PLACEHOLDERS, "对话评分"),
        BuiltinPrompt("task", "post_visit_review", post_visit_review.POST_VISIT_REVIEW_PROMPT, _TASK_PLACEHOLDERS, "访后机会推进卡"),
    ]


def _system_router_risk_entries() -> list[BuiltinPrompt]:
    from sales_agent.prompts.risk_check_prompt import RISK_CHECK_PROMPT
    from sales_agent.prompts.system import SYSTEM_CONSTRAINT
    from sales_agent.prompts.task_router_prompt import TASK_ROUTER_PROMPT

    return [
        BuiltinPrompt("system", "system_constraint", SYSTEM_CONSTRAINT, (), "系统约束（Agent 人设与硬性边界）"),
        BuiltinPrompt("router", "task_router", TASK_ROUTER_PROMPT, ("message",), "任务路由 LLM 兜底分类器"),
        BuiltinPrompt("risk", "risk_check", RISK_CHECK_PROMPT, ("message", "answer"), "风险检查 LLM 合规复核"),
    ]


def _coach_entries() -> list[BuiltinPrompt]:
    """Coach 子系统 prompt：每日评估 + 快速会话（小赢欣赏 / 卡点破框）。"""
    from sales_agent.prompts.coach_daily_evaluation import COACH_DAILY_EVALUATION_PROMPT
    from sales_agent.prompts.coach_quick import (
        COACH_DAILY_EVAL_SYSTEM,
        SB_CARD_TEMPLATE,
        SB_SPLIT_TEMPLATE,
        SB_SYSTEM,
        SW_CARD_TEMPLATE,
        SW_SYSTEM,
    )

    return [
        BuiltinPrompt("coach", "coach_daily_eval", COACH_DAILY_EVALUATION_PROMPT, ("conversation_block",), "Coach 每日能力评估"),
        BuiltinPrompt("coach", "coach_daily_eval_system", COACH_DAILY_EVAL_SYSTEM, (), "Coach 评估 system 消息"),
        BuiltinPrompt("coach", "coach_sw_system", SW_SYSTEM, (), "小赢欣赏 system 人设"),
        BuiltinPrompt("coach", "coach_sb_system", SB_SYSTEM, (), "卡点破框 system 人设"),
        BuiltinPrompt("coach", "coach_sw_card", SW_CARD_TEMPLATE, ("small_win", "strength", "gratitude", "energy_sentence"), "小赢卡出卡模板"),
        BuiltinPrompt("coach", "coach_sb_split", SB_SPLIT_TEMPLATE, ("sales_input", "user_split"), "事实/解释拆分模板"),
        BuiltinPrompt("coach", "coach_sb_card", SB_CARD_TEMPLATE, ("sales_input", "split_text", "possibilities_attempt"), "破框卡出卡模板"),
    ]


BUILTIN_PROMPTS: list[BuiltinPrompt] = [
    *_task_entries(),
    *_system_router_risk_entries(),
    *_coach_entries(),
]


def get_builtin(category: str, key: str) -> BuiltinPrompt | None:
    """按 (category, key) 取内置 prompt。"""
    for b in BUILTIN_PROMPTS:
        if b.category == category and b.key == key:
            return b
    return None


def required_placeholders_for(category: str, key: str) -> list[str]:
    """取某 (category, key) 的必须占位符；未注册时默认要求 {message}。"""
    b = get_builtin(category, key)
    if b is not None:
        return list(b.required_placeholders)
    return ["message"]


def extend_with_coach(entries: list[BuiltinPrompt]) -> None:
    """阶段 3 coach 接入时调用：把 coach 类内置 prompt 追加到注册表。"""
    existing = {(b.category, b.key) for b in BUILTIN_PROMPTS}
    for entry in entries:
        if (entry.category, entry.key) not in existing:
            BUILTIN_PROMPTS.append(entry)
            existing.add((entry.category, entry.key))
