"""Evidence Router — 意图证据路由服务。

输入经过上下文解析后的独立查询，输出意图类型和知识检索策略决策。
遵循 ``resolve_context`` 的 try-retry-fallback 模式。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.call_params import get_call_params
from sales_agent.prompts.evidence_router_prompt import EVIDENCE_ROUTER_PROMPT
from sales_agent.services.prompt_resolver_helper import resolve_router_prompt
from sales_agent.services.structured_router_output import EvidenceDecision, parse_model_json
from sales_agent.services.task_router import (
    ALL_TASK_TYPES,
    GENERAL_COACHING,
    _FACT_SIGNAL_PATTERNS,
    apply_evidence_policy_guard as _apply_policy_guard_fields,
)

logger = logging.getLogger(__name__)


def apply_evidence_policy_guard(
    query: str,
    decision: EvidenceDecision,
) -> EvidenceDecision:
    """对 LLM 输出的证据决策应用本地策略守卫。

    根据查询中的事实信号词或非事实信号词，强制调整
    ``knowledge_policy`` 和 ``response_mode``。

    不会暴露任何节点名、工具名或内部路由细节 — 仅修正 policy 级别。

    Parameters
    ----------
    query :
        原始用户查询文本（用于信号检测）。
    decision :
        LLM 输出的原始决策。

    Returns
    -------
    EvidenceDecision
        经过策略守卫修正后的决策（就地修改并返回同一对象）。
    """
    knowledge_policy, response_mode, retrieval_query, reason_code = (
        _apply_policy_guard_fields(
            query,
            decision.knowledge_policy,
            decision.response_mode,
            decision.retrieval_query,
            decision.reason_code,
        )
    )
    decision.knowledge_policy = knowledge_policy
    decision.response_mode = response_mode
    decision.retrieval_query = retrieval_query
    decision.reason_code = reason_code
    return decision


def _deterministic_fallback(
    standalone_query: str,
) -> EvidenceDecision:
    """LLM 两次输出都无法解析时的确定性兜底策略。

    根据查询中的信号词分配意图和策略。
    """
    has_fact_signal = any(p.search(standalone_query) for p in _FACT_SIGNAL_PATTERNS)

    if has_fact_signal:
        return EvidenceDecision(
            intent=GENERAL_COACHING,
            response_mode="retrieve",
            knowledge_policy="required",
            knowledge_scope=[],
            retrieval_query=standalone_query,
            confidence=0.5,
            reason_code="evidence_router_fallback_required",
        )

    return EvidenceDecision(
        intent=GENERAL_COACHING,
        response_mode="direct",
        knowledge_policy="none",
        knowledge_scope=[],
        retrieval_query=None,
        confidence=0.5,
        reason_code="evidence_router_fallback_none",
    )


async def route_intent_evidence(
    *,
    standalone_query: str,
    chat_model: Any,
    stable_identity: dict[str, str] | None = None,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    recent_context: str | None = None,
) -> EvidenceDecision:
    """路由意图与证据策略。

    分析独立查询，确定任务意图和知识检索策略。
    内部重试逻辑：首次解析失败时追加 schema-error 提示重试；
    两次均失败则使用 :func:`_deterministic_fallback` 兜底。

    Parameters
    ----------
    standalone_query :
        经过上下文解析器重写后的自包含独立查询。
    chat_model :
        支持 ``generate(messages, temperature, max_tokens)`` 的模型实例。
    stable_identity :
        稳定身份标识（可选，用于 prompt 个性化）。
    db :
        数据库会话；非空且 *tenant_id* 有值时走 PromptRegistry 三级回退
        （运营后台编辑生效），否则回退到模块常量 :data:`EVIDENCE_ROUTER_PROMPT`。
    tenant_id, agent_id :
        租户 / Agent 标识，用于 PromptRegistry 解析。
    recent_context :
        最近对话上下文（可选）。提供时作为用户消息前缀，
        帮助 LLM 对简短跟进消息进行准确的意图分类。

    Returns
    -------
    EvidenceDecision
        路由决策，包含 intent、knowledge_policy、response_mode 等。
    """
    system_prompt = await resolve_router_prompt(
        db,
        "evidence_router",
        tenant_id,
        agent_id,
        default=EVIDENCE_ROUTER_PROMPT,
    )
    if recent_context:
        user_content = f"{recent_context}\n\n用户消息：{standalone_query}\n"
    else:
        user_content = f"用户消息：{standalone_query}\n"
    if stable_identity:
        user_content += f"\n用户身份：{stable_identity.get('name', '未知')}\n"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    for attempt in range(2):
        try:
            p = get_call_params("evidence_router")
            response = await chat_model.generate(
                messages=messages,
                temperature=p.temperature,
                max_tokens=p.max_tokens,
            )
            decision = parse_model_json(response, EvidenceDecision)

            # 验证 intent 在有效范围内
            if decision.intent not in ALL_TASK_TYPES:
                logger.warning(
                    "evidence_router: unknown intent '%s', falling back to %s",
                    decision.intent,
                    GENERAL_COACHING,
                )
                decision.intent = GENERAL_COACHING
                decision.reason_code = "unknown_intent_fallback"

            # 应用策略守卫
            decision = apply_evidence_policy_guard(standalone_query, decision)

            return decision

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "evidence_router parse failure (attempt %d/2): %s",
                attempt + 1,
                exc,
            )
            if attempt == 0:
                messages.append({
                    "role": "user",
                    "content": "输出格式不符合 JSON 规范，请仅输出一个合法的 JSON 对象，不要包含其他任何内容。",
                })
                continue

            # 第二次失败，使用确定性兜底
            return _deterministic_fallback(standalone_query)

    # 不应到达此处，但为类型安全保留
    return _deterministic_fallback(standalone_query)
