"""任务路由服务：识别用户输入对应的任务类型。

采用"规则优先 + LLM 分类兜底"的设计。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sales_agent.llm.call_params import get_call_params
from sales_agent.llm.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

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
    needs_retrieval: bool = False
    needs_clarification: bool = False
    router_type: str = "rule"           # "rule" | "llm" | "fallback"
    llm_router_called: bool = False     # 是否调用了 LLM router
    knowledge_policy: str = "none"      # "none" | "optional" | "required"
    knowledge_scope: list[str] = field(default_factory=list)
    retrieval_query: str | None = None

    def __post_init__(self) -> None:
        """初始化后推导 needs_retrieval 兼容字段。"""
        # 从 knowledge_policy 推导 needs_retrieval
        object.__setattr__(self, "needs_retrieval", self.knowledge_policy != "none")

    @property
    def should_proceed(self) -> bool:
        """是否可以直接执行。"""
        return self.confidence >= 0.45


# --- 策略守卫：信号关键词 ---

# 事实信号关键词 — 检测到这些信号时强制 required/retrieve
_FACT_SIGNAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(产品|功能|方案|服务)"),
    re.compile(r"(公司|企业)"),
    re.compile(r"(价格|多少钱|怎么收费|报价|费用|成本|预算)"),
    re.compile(r"(政策|制度|规定|流程|售后|保障)"),
    re.compile(r"(案例|成功故事|客户见证|效果)"),
    re.compile(r"(竞品|竞争对手|对比|区别)"),
    re.compile(r"(合同|合约|协议|签约|条款)"),
    re.compile(r"(交付|实施|上线|部署|售后|服务)"),
]

# 非事实信号关键词 — 检测到这些信号时强制 none/direct
_NON_FACT_SIGNAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(你好|嗨|hi|hello|hey|您好|早上好|下午好|晚上好)$"),
    re.compile(r"(焦虑|沮丧|崩溃|担心|害怕|紧张|压力|没信心|想放弃|郁闷|烦躁)"),
    re.compile(r"(说得对|好的|明白了|知道了|收到|谢谢|感谢|懂了|嗯|好的吧)"),
]


def apply_evidence_policy_guard(
    query: str,
    knowledge_policy: str,
    response_mode: str,
    retrieval_query: str | None,
    reason_code: str,
) -> tuple[str, str, str | None, str]:
    """对知识策略决策应用本地策略守卫。

    根据查询中的事实信号词或非事实信号词，强制调整
    ``knowledge_policy`` 和 ``response_mode``。

    不会暴露任何节点名、工具名或内部路由细节 — 仅修正 policy 级别。

    Parameters
    ----------
    query :
        原始用户查询文本（用于信号检测）。
    knowledge_policy :
        原始的 knowledge_policy 值。
    response_mode :
        原始的 response_mode 值。
    retrieval_query :
        原始的 retrieval_query 值。
    reason_code :
        原始的 reason_code 值。

    Returns
    -------
    tuple[str, str, str | None, str]
        (knowledge_policy, response_mode, retrieval_query, reason_code)
    """
    # 检测事实信号 — 升级策略（none 或 optional 均升级到 required）
    has_fact_signal = any(p.search(query) for p in _FACT_SIGNAL_PATTERNS)

    if has_fact_signal and knowledge_policy in ("none", "optional"):
        knowledge_policy = "required"
        response_mode = "retrieve"
        reason_code = "policy_guard_upgraded_to_required"

    # 检测非事实信号 — 降级策略（但已 required/web 的不降级；web 是离域联网决策，粘性）
    has_non_fact_signal = any(p.search(query) for p in _NON_FACT_SIGNAL_PATTERNS)

    if has_non_fact_signal and knowledge_policy not in ("required", "web"):
        knowledge_policy = "none"
        response_mode = "direct"
        if reason_code == "policy_guard_upgraded_to_required":
            pass  # 事实信号优先于非事实信号
        else:
            reason_code = "policy_guard_downgraded_to_none"

    # 如果 required 但没有 retrieval_query，使用原始查询补位
    if knowledge_policy == "required" and not retrieval_query:
        retrieval_query = query

    return knowledge_policy, response_mode, retrieval_query, reason_code


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
         "和竞品.*区别", "功能有哪些",
         "包含什么", "包含哪些", "有什么", "有哪些", "是什么", "什么是",
         "介绍一下", "介绍", "价格", "多少钱", "怎么收费", "赔付", "保障",
         "承诺", "售后", "服务政策", "产品介绍", "公司介绍"],
        [r"我们产品(的)?", r"资料里(有|说)", r"有没有案例", r"优势是什么", r"功能有哪些",
         r"包含(什么|哪些)", r"有(什么|哪些)", r"什么是", r"介绍一下",
         r"(价格|多少钱|怎么收费)", r"(赔付|保障|承诺|售后)(政策|标准)?", r"公司介绍"],
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
    POST_VISIT_REVIEW,         # 1. 访后复盘（机会推进卡）— 最具体的工作流请求
    VISIT_PREPARATION,         # 2. 访前准备（明确的工作流请求）
    CONVERSATION_REVIEW,       # 3. 复盘（通用复盘）
    CONVERSATION_SCORING,      # 4. 对话评分（与复盘相关但更具体）
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

    needs_clarification = best_conf < 0.45

    # 从 TASK_DEFAULT_RETRIEVAL 推导 knowledge_policy
    default_needs_retrieval = TASK_DEFAULT_RETRIEVAL.get(best_type, False)
    knowledge_policy = "required" if default_needs_retrieval else "none"

    return RouteResult(
        task_type=best_type,
        confidence=best_conf,
        knowledge_policy=knowledge_policy,
        needs_clarification=needs_clarification,
    )


# --- LLM 兜底路由 ---

# router prompt 已迁移至 config/prompts.yaml（get_prompt("router", "task_router")）
_DEFAULT_ROUTER_PROMPT = None  # 由 _llm_route 内部惰性 resolve


class _KeepMissingDict(dict):
    """``str.format_map`` 安全映射：未知占位符原样保留为 ``{key}``。

    task_router 的 DB 版 prompt 含三类花括号：
    1. ``{message}`` —— 真占位符，需替换为用户输入；
    2. ``{{...}}`` —— JSON 示例的 escape，format 会还原为 ``{...}``；
    3. ``{群聊/私聊}`` ``{发送者姓名}`` 等 —— 作者漏 escape 的字面花括号。

    普通 ``.format(message=...)`` 把第 3 类当未知占位符抛 ``KeyError``，
    导致 LLM 路由每次必崩、回退规则路由。本映射让未知 key 原样保留，
    三类花括号都能正确处理，无需改动 DB prompt 数据。
    """

    def __missing__(self, key: str) -> str:  # noqa: D401
        return "{" + key + "}"


# DB 版 task_router prompt 输出 intent schema（chat/deep/follow_up/emotion/creative），
# 映射到内部 task_type。deep = 需检索的深度问题 → knowledge_qa；emotion → emotional_support；
# creative = 写话术/方案 → script_generation；chat/follow_up 无需检索 → 通用兜底。
_INTENT_TO_TASK: dict[str, str] = {
    "chat": GENERAL_COACHING,
    "deep": KNOWLEDGE_QA,
    "follow_up": GENERAL_COACHING,
    "emotion": EMOTIONAL_SUPPORT,
    "creative": SCRIPT_GENERATION,
}


def _extract_first_json(text: str) -> dict | None:
    """从文本中提取第一个完整 JSON 对象（支持嵌套花括号与字符串内的花括号）。

    ``re.search(r"\\{[^}]+\\}", ...)`` 在嵌套 JSON（如 prompt 要求的
    ``channel_queries`` 结构）上会截断出非法 JSON。本函数按花括号深度配对
    扫描，并在字符串字面量内跳过花括号，确保提取完整对象。
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start == -1:
        return None
    import json

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def _llm_route(
    message: str,
    chat_model: Any,
    router_prompt: str | None = None,
) -> RouteResult | None:
    """使用 LLM 分类兜底。

    Args:
        router_prompt: 调用方经 ``PromptRegistry`` 解析后的 router 模板；
            为 None 时回退到内置默认。
    """
    try:
        prompt = (router_prompt or get_prompt("router", "task_router").template).format_map(
            _KeepMissingDict(message=message)
        )
        p = get_call_params("task_router")
        response = await chat_model.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=p.temperature,
            max_tokens=p.max_tokens,
        )
        # 解析 JSON 响应：DB 版 prompt 输出 intent schema（含嵌套 channel_queries），
        # 用平衡花括号提取完整 JSON，再映射 intent → task_type；兼容旧 task_type schema。
        data = _extract_first_json(response)
        if data:
            task_type = GENERAL_COACHING
            if data.get("task_type") in ALL_TASK_TYPES:
                task_type = data["task_type"]
            else:
                intent = str(data.get("intent", "")).strip().lower()
                task_type = _INTENT_TO_TASK.get(intent, GENERAL_COACHING)
            confidence = float(data.get("confidence", 0.65))
            # knowledge_policy：优先用 prompt 显式给的，否则按 task_type 默认推导
            knowledge_policy = data.get("knowledge_policy")
            if knowledge_policy not in ("none", "optional", "required"):
                if knowledge_policy is None:
                    needs_retrieval = TASK_DEFAULT_RETRIEVAL.get(task_type, False)
                else:
                    needs_retrieval = bool(data.get("needs_retrieval", False))
                knowledge_policy = "required" if needs_retrieval else "none"
            return RouteResult(
                task_type=task_type,
                confidence=confidence,
                knowledge_policy=knowledge_policy,
                needs_clarification=confidence < 0.45,
            )
    except Exception:
        logger.warning("LLM route failed, falling back to rules", exc_info=True)
    return None


async def route_task(
    message: str,
    chat_model: Any = None,
    confidence_threshold: float | None = None,
    db: Any = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    router_prompt: str | None = None,
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
        # 解析 router prompt：优先调用方传入，否则从 YAML 加载
        if router_prompt is None:
            router_prompt = get_prompt("router", "task_router").template
        llm_result = await _llm_route(message, chat_model, router_prompt)
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
        needs_clarification=True,
        router_type="fallback",
        llm_router_called=False,
    )


def route_task_rules_only(message: str) -> RouteResult:
    """仅使用规则路由，不调用 LLM。用于 fast path。

    Args:
        message: 用户输入消息

    Returns:
        RouteResult 包含 task_type, confidence, knowledge_policy
    """
    hits = _match_rules(message)
    if hits:
        result = _resolve_priority(hits, message)
        result.router_type = "rule"
        result.llm_router_called = False
        # 对规则结果应用策略守卫
        knowledge_policy, _, retrieval_query, _ = (
            apply_evidence_policy_guard(
                message,
                result.knowledge_policy,
                "retrieve" if result.knowledge_policy == "required" else "direct",
                result.retrieval_query,
                "rule_match",
            )
        )
        result.knowledge_policy = knowledge_policy
        result.retrieval_query = retrieval_query
        return result

    return RouteResult(
        task_type=GENERAL_COACHING,
        confidence=0.3,
        needs_clarification=True,
        router_type="rule",
        llm_router_called=False,
    )
