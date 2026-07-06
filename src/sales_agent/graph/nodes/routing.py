"""Task routing node.

规则优先路由；当 ``enable_llm_router`` 打开且 ``chat_model`` 可用时，
对规则置信度不足的输入调 LLM 分类兜底（service 层 ``route_task``）。
LLM 失败回退规则结果。默认 ``enable_llm_router=False``，行为与纯规则一致。
"""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from sales_agent.core.config import get_settings
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import (
    apply_evidence_policy_guard,
    route_task,
    route_task_rules_only,
)

logger = logging.getLogger(__name__)


async def routing_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Route the user message to a task type.

    When ``precomputed_route=True`` (set by Online Graph's evidence routing),
    return existing ``task_type``, ``needs_retrieval``, and
    ``knowledge_policy`` fields without calling ``route_task`` — the upstream
    router has already made its decision.

    Otherwise:
      - ``enable_llm_router`` 关闭或无 ``chat_model`` → 规则路由
        (``route_task_rules_only``，自带 ``apply_evidence_policy_guard``)。
      - ``enable_llm_router`` 打开且有 ``chat_model`` → ``route_task``（规则
        优先 + LLM 兜底）。LLM 路径不经 guard，此处对结果显式补一次
        ``apply_evidence_policy_guard`` 修正 ``knowledge_policy``，与规则路径
        行为对齐；LLM 失败回退规则结果。

    Args:
        state: Current graph state with ``message`` populated.
        runtime: LangGraph runtime，context 含 ``chat_model``/``db``。

    Returns:
        Dict with ``task_type``, ``route_confidence``, ``needs_retrieval``,
        ``needs_clarification``, ``knowledge_policy``。
    """
    if state.get("precomputed_route"):
        return {
            "task_type": state.get("task_type"),
            "needs_retrieval": state.get("needs_retrieval", False),
            "knowledge_policy": state.get("knowledge_policy"),
            "route_confidence": state.get("route_confidence", 0.5),
        }

    message = state["message"]
    settings = get_settings()
    chat_model = runtime.context.get("chat_model")
    db = runtime.context.get("db")
    tenant_id = state.get("tenant_id")
    agent_id = state.get("agent_id")

    route_result = None
    if settings.path_router.enable_llm_router and chat_model is not None:
        try:
            route_result = await route_task(
                message,
                chat_model=chat_model,
                db=db,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            # LLM 路径不经 apply_evidence_policy_guard，此处显式补一次，
            # 修正 knowledge_policy（evidence_gate 下游消费它）。
            knowledge_policy, _response_mode, retrieval_query, _reason = (
                apply_evidence_policy_guard(
                    message,
                    route_result.knowledge_policy,
                    "retrieve" if route_result.knowledge_policy == "required" else "direct",
                    route_result.retrieval_query,
                    "llm_route",
                )
            )
            route_result.knowledge_policy = knowledge_policy
            route_result.retrieval_query = retrieval_query
        except Exception:
            logger.warning("LLM route failed, falling back to rules-only", exc_info=True)
            route_result = None

    if route_result is None:
        route_result = route_task_rules_only(message)

    return {
        "task_type": route_result.task_type,
        "route_confidence": route_result.confidence,
        "needs_retrieval": route_result.needs_retrieval,
        "needs_clarification": route_result.needs_clarification,
        "knowledge_policy": route_result.knowledge_policy,
    }
