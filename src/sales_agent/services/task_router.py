"""任务路由服务：识别用户输入对应的任务类型。

采用"规则优先 + LLM 分类兜底"的设计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 任务类型常量 — Phase A
EMOTIONAL_SUPPORT = "emotional_support"
KNOWLEDGE_QA = "knowledge_qa"
SCRIPT_GENERATION = "script_generation"
OBJECTION_HANDLING = "objection_handling"
CONVERSATION_REVIEW = "conversation_review"
GENERAL_COACHING = "general_sales_coaching"

# 任务类型常量 — Phase B: Sales Workflow Intelligence
VISIT_PREPARATION = "visit_preparation"
FOLLOW_UP_PLANNING = "follow_up_planning"
CUSTOMER_CONTEXT_SUMMARY = "customer_context_summary"
DEAL_ADVANCEMENT = "deal_advancement"
CONVERSATION_SCORING = "conversation_scoring"
POST_VISIT_REVIEW = "post_visit_review"

# Phase B 新增的任务类型集合，方便批量判断
WORKFLOW_TASK_TYPES = {
    VISIT_PREPARATION,
    FOLLOW_UP_PLANNING,
    CUSTOMER_CONTEXT_SUMMARY,
    DEAL_ADVANCEMENT,
    CONVERSATION_SCORING,
    POST_VISIT_REVIEW,
}

ALL_TASK_TYPES = [
    EMOTIONAL_SUPPORT,
    KNOWLEDGE_QA,
    SCRIPT_GENERATION,
    OBJECTION_HANDLING,
    CONVERSATION_REVIEW,
    GENERAL_COACHING,
    VISIT_PREPARATION,
    FOLLOW_UP_PLANNING,
    CUSTOMER_CONTEXT_SUMMARY,
    DEAL_ADVANCEMENT,
    CONVERSATION_SCORING,
    POST_VISIT_REVIEW,
]

# 是否需要 RAG 的默认值
TASK_DEFAULT_RETRIEVAL = {
    EMOTIONAL_SUPPORT: False,
    KNOWLEDGE_QA: True,
    SCRIPT_GENERATION: False,
    OBJECTION_HANDLING: False,
    CONVERSATION_REVIEW: False,
    GENERAL_COACHING: False,
    VISIT_PREPARATION: False,
    FOLLOW_UP_PLANNING: False,
    CUSTOMER_CONTEXT_SUMMARY: False,
    DEAL_ADVANCEMENT: False,
    CONVERSATION_SCORING: False,
    POST_VISIT_REVIEW: False,
}


@dataclass
class RouteResult:
    """任务路由结果。"""

    task_type: str
    confidence: float
    needs_retrieval: bool
    needs_clarification: bool = False
    router_type: str = "rule"           # "rule" | "llm" | "fallback"
    llm_router_called: bool = False     # 是否调用了 LLM router

    @property
    def should_proceed(self) -> bool:
        """是否可以直接执行。"""
        return self.confidence >= 0.45


# --- 规则匹配 ---

# 每条规则：(task_type, 关键词列表, 正则列表)
_RULE_TABLE: list[tuple[str, list[str], list[str]]] = [
    (
        CONVERSATION_REVIEW,
        ["复盘", "这段聊天", "我这样说对吗", "帮我分析这段", "哪里有问题", "沟通记录"],
        [r"帮我复盘", r"这段(聊天|沟通|对话)", r"我这样说(对吗|好不好)", r"分析这段"],
    ),
    (
        SCRIPT_GENERATION,
        ["帮我写", "生成话术", "怎么发给客户", "写一段", "写个话术", "催客户", "跟进话术"],
        [r"帮我写(一段|一个|一封)?", r"生成?(话术|回复|消息)", r"怎么发(给客户)?", r"催客户"],
    ),
    (
        OBJECTION_HANDLING,
        ["客户说太贵", "客户担心", "客户质疑", "竞品更便宜", "嫌贵", "太贵了",
         "觉得贵", "价格太高", "竞争对手"],
        [r"客户说太贵", r"嫌贵", r"竞品.*便宜", r"客户(担心|质疑|觉得)", r"太贵了"],
    ),
    (
        KNOWLEDGE_QA,
        ["我们产品", "资料里", "有没有案例", "优势是什么", "产品特点", "案例",
         "和竞品.*区别", "功能有哪些"],
        [r"我们产品(的)?", r"资料里(有|说)", r"有没有案例", r"优势是什么", r"功能有哪些"],
    ),
    (
        EMOTIONAL_SUPPORT,
        ["客户不回", "没希望", "很焦虑", "被拒绝", "很沮丧", "想放弃",
         "做不下去了", "没信心", "压力很大", "很崩溃"],
        [r"客户.*不回", r"没(希望|戏)了", r"很(焦虑|沮丧|崩溃)", r"被拒绝", r"想放弃"],
    ),
    # --- Phase B: Sales Workflow 规则 ---
    (
        VISIT_PREPARATION,
        ["拜访提纲", "拜访准备", "明天拜访", "准备拜访", "拜访客户",
         "拜访前", "帮我准备拜访", "见客户前",
         "我要去拜访", "去拜访客户", "准备去见客户", "马上拜访",
         "访前", "访前准备", "作战卡", "客户沟通作战卡",
         "访前作战卡", "见客户前帮我想清楚"],
        [r"拜访(准备|提纲|客户|前)", r"准备.*拜访", r"见客户(之前|前)",
         r"我要去拜访", r"去拜访客户", r"准备去见客户",
         r"访前(准备|作战卡)?", r"作战卡", r"客户沟通作战卡"],
    ),
    (
        POST_VISIT_REVIEW,
        ["刚聊完客户", "刚见完客户", "访后", "沟通结束了", "聊完了",
         "见完客户", "拜访完了", "客户聊完了", "刚结束拜访",
         "访后总结", "访后复盘", "机会推进", "访后机会推进",
         "访后机会推进卡", "聊完帮我复盘", "有没有下一步"],
        [r"刚(聊完|见完|结束).*客户", r"访后(复盘|总结|机会推进)?",
         r"拜访完", r"沟通结束(了)?",
         r"客户(聊完|沟通总结)", r"聊完.*复盘", r"有没有下一步"],
    ),
    (
        FOLLOW_UP_PLANNING,
        ["跟进计划", "下一步怎么跟", "跟进方案", "跟进策略", "怎么跟",
         "跟进节奏", "跟进安排"],
        [r"跟进(计划|方案|策略|节奏|安排)", r"下一步怎么"],
    ),
    (
        CUSTOMER_CONTEXT_SUMMARY,
        ["客户情况整理", "整理客户信息", "客户背景", "客户画像", "客户分析",
         "客户档案", "客户信息整理", "梳理客户"],
        [r"整理.*客户", r"客户.*画像", r"客户.*背景.*整理", r"梳理.*客户", r"客户.*档案"],
    ),
    (
        DEAL_ADVANCEMENT,
        ["怎么推进", "成交推进", "推进成交", "怎么签约", "推动下单",
         "逼单", "促单", "怎么成交"],
        [r"怎么推进", r"推进.*成交", r"怎么签约", r"逼单", r"促单", r"怎么成交"],
    ),
    (
        CONVERSATION_SCORING,
        ["对话评分", "销售评分", "给我打分", "打几分", "打个分",
         "通话评分", "沟通评分", "评分标准", "打分评估"],
        [r"给我.*打分", r"对话.*评分", r"销售.*评分", r"打个?分", r"打几分", r"评分标准"],
    ),
]


def _match_rules(message: str) -> list[tuple[str, float]]:
    """用规则表匹配，返回 [(task_type, confidence), ...]。"""
    hits: list[tuple[str, float]] = []
    for task_type, keywords, patterns in _RULE_TABLE:
        keyword_hits = sum(1 for kw in keywords if kw in message)
        pattern_hits = sum(1 for p in patterns if re.search(p, message))
        total = keyword_hits + pattern_hits
        if total > 0:
            # 规则匹配的置信度较高
            confidence = min(0.95, 0.7 + total * 0.05)
            hits.append((task_type, confidence))
    return hits


# --- 多任务命中优先级 ---
_PRIORITY_ORDER = [
    CONVERSATION_REVIEW,       # 1. 复盘优先
    CONVERSATION_SCORING,      # 2. 对话评分（与复盘相关但更具体）
    VISIT_PREPARATION,         # 3. 访前准备（明确的工作流请求）
    POST_VISIT_REVIEW,         # 4. 访后复盘（机会推进卡）
    FOLLOW_UP_PLANNING,        # 5. 跟进计划
    CUSTOMER_CONTEXT_SUMMARY,  # 6. 客户上下文整理
    DEAL_ADVANCEMENT,          # 7. 成交推进
    SCRIPT_GENERATION,         # 8. 明确要求写话术
    OBJECTION_HANDLING,        # 9. 明确提到客户异议
    KNOWLEDGE_QA,              # 10. 明确问企业资料
    EMOTIONAL_SUPPORT,         # 11. 情绪表达
    GENERAL_COACHING,          # 12. 兜底
]


def _resolve_priority(hits: list[tuple[str, float]], message: str) -> RouteResult:
    """多任务命中时按优先级选择。"""
    if not hits:
        return RouteResult(
            task_type=GENERAL_COACHING,
            confidence=0.3,
            needs_retrieval=False,
            needs_clarification=True,
        )

    # 按优先级排序
    def priority_key(item: tuple[str, float]) -> int:
        try:
            return _PRIORITY_ORDER.index(item[0])
        except ValueError:
            return len(_PRIORITY_ORDER)

    sorted_hits = sorted(hits, key=priority_key)
    best_type, best_conf = sorted_hits[0]

    # 情绪表达 + 具体任务同时出现时，在目标任务中加入情绪回应标记
    # 但不单独路由为 emotional_support
    has_emotion = any(h[0] == EMOTIONAL_SUPPORT for h in sorted_hits)
    if has_emotion and best_type != EMOTIONAL_SUPPORT:
        # 保留高优先级的任务类型，但置信度微调
        pass

    needs_retrieval = TASK_DEFAULT_RETRIEVAL.get(best_type, False)
    needs_clarification = best_conf < 0.45

    return RouteResult(
        task_type=best_type,
        confidence=best_conf,
        needs_retrieval=needs_retrieval,
        needs_clarification=needs_clarification,
    )


# --- LLM 兜底路由 ---

_LLM_ROUTER_PROMPT = """你是一个销售助手的任务分类器。请根据用户的输入判断任务类型。

任务类型：
- emotional_support: 销售情绪支持和行动建议（客户不回、焦虑、想放弃等）
- knowledge_qa: 企业知识库问答（问产品、案例、方案、FAQ、竞品）
- script_generation: 销售话术生成（要求写话术、回复、跟进文案）
- objection_handling: 客户异议处理（客户嫌贵、质疑、比竞品、拒绝）
- conversation_review: 销售沟通复盘（粘贴聊天记录、分析对话、找问题）
- visit_preparation: 拜访准备（准备拜访客户、拜访提纲、见客户前准备）
- follow_up_planning: 跟进计划（跟进策略、下一步怎么跟、客户没回复后怎么办）
- customer_context_summary: 客户上下文整理（整理客户信息、客户画像、梳理客户背景）
- deal_advancement: 成交推进（怎么推进、逼单、促单、推动签约）
- conversation_scoring: 对话评分（给对话打分、销售评分、通话评估）
- post_visit_review: 访后机会推进（刚见完客户、刚聊完客户、沟通结束后梳理下一步行动、访后复盘、机会推进卡）
- general_sales_coaching: 通用销售训练和建议（无法归类到以上类型）

用户输入：{message}

请以 JSON 格式回复：
{{"task_type": "类型", "confidence": 0.0-1.0, "needs_retrieval": true/false}}
"""


async def _llm_route(message: str, chat_model: Any) -> RouteResult | None:
    """使用 LLM 分类兜底。"""
    try:
        prompt = _LLM_ROUTER_PROMPT.format(message=message)
        response = await chat_model.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        # 解析 JSON 响应
        import json

        # 尝试提取 JSON
        json_match = re.search(r"\{[^}]+\}", response)
        if json_match:
            data = json.loads(json_match.group())
            task_type = data.get("task_type", GENERAL_COACHING)
            if task_type not in ALL_TASK_TYPES:
                task_type = GENERAL_COACHING
            confidence = float(data.get("confidence", 0.5))
            needs_retrieval = bool(data.get("needs_retrieval", False))
            return RouteResult(
                task_type=task_type,
                confidence=confidence,
                needs_retrieval=needs_retrieval,
                needs_clarification=confidence < 0.45,
            )
    except Exception:
        pass
    return None


async def route_task(
    message: str,
    chat_model: Any = None,
    confidence_threshold: float | None = None,
) -> RouteResult:
    """路由任务类型。

    Args:
        message: 用户输入消息
        chat_model: LLM chat model，用于兜底分类。可选。
        confidence_threshold: LLM router 调用阈值，默认从配置读取。

    Returns:
        RouteResult 包含 task_type, confidence, needs_retrieval, needs_clarification,
                     router_type, llm_router_called
    """
    if confidence_threshold is None:
        try:
            from sales_agent.core.config import get_settings
            confidence_threshold = get_settings().path_router.llm_router_confidence_threshold
        except Exception:
            confidence_threshold = 0.75

    # 1. 规则匹配
    hits = _match_rules(message)

    # 2. 有规则命中，高置信度直接返回（不调 LLM）
    if hits:
        result = _resolve_priority(hits, message)
        if result.confidence >= confidence_threshold:
            result.router_type = "rule"
            result.llm_router_called = False
            return result

    # 3. 规则匹配置信度不足或无匹配，尝试 LLM 兜底
    if chat_model is not None:
        llm_result = await _llm_route(message, chat_model)
        if llm_result is not None:
            llm_result.router_type = "llm"
            llm_result.llm_router_called = True
            # 如果规则也有结果，取置信度更高的
            if hits:
                rule_result = _resolve_priority(hits, message)
                rule_result.router_type = "rule"
                rule_result.llm_router_called = True  # LLM 被调用了，只是没用它的结果
                if rule_result.confidence >= llm_result.confidence:
                    return rule_result
            return llm_result

    # 4. 完全兜底
    if hits:
        result = _resolve_priority(hits, message)
        result.router_type = "rule"
        result.llm_router_called = False
        return result

    return RouteResult(
        task_type=GENERAL_COACHING,
        confidence=0.3,
        needs_retrieval=False,
        needs_clarification=True,
        router_type="fallback",
        llm_router_called=False,
    )


def route_task_rules_only(message: str) -> RouteResult:
    """仅使用规则路由，不调用 LLM。用于 fast path。

    Args:
        message: 用户输入消息

    Returns:
        RouteResult 包含 task_type, confidence, needs_retrieval
    """
    hits = _match_rules(message)
    if hits:
        result = _resolve_priority(hits, message)
        result.router_type = "rule"
        result.llm_router_called = False
        return result

    return RouteResult(
        task_type=GENERAL_COACHING,
        confidence=0.3,
        needs_retrieval=False,
        needs_clarification=True,
        router_type="rule",
        llm_router_called=False,
    )
