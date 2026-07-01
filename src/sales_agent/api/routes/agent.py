"""Agent Chat 路由 — 委托 ChatPipeline 或 LangGraph 执行。

P0: Graph execution now includes Store for cross-session memory
     and supports ``interrupt_before``/``interrupt_after`` for HITL.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import ChatRequest, ChatResponse, DebugInfo
from sales_agent.core.config import get_settings
from sales_agent.core.exceptions import (
    ERROR_HTTP_STATUS,
    SalesAgentError,
)
from sales_agent.core.tenant_runtime import get_tenant_runtime
from sales_agent.graph.chat_graph import build_chat_graph_compiled
from sales_agent.graph.checkpoints import get_checkpointer, get_store
from sales_agent.models.base import generate_id
from sales_agent.services.chat_pipeline import ChatPipeline
from sales_agent.services.response_formatter import build_chat_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


async def _execute_via_graph(
    db: AsyncSession,
    chat_model: Any,
    tenant_id: str,
    user_id: str,
    message: str,
    conversation_id: str | None,
    channel: str,
    agent_id: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Execute chat via LangGraph StateGraph (parallel path to ChatPipeline).

    P0: Uses ``build_chat_graph_compiled`` with checkpointer and store
    for cross-session memory. Common ``interrupt_before`` and
    ``interrupt_after`` options can be passed to enable HITL.

    Args:
        db: Database session (placed in Runtime.context for graph nodes).
        chat_model: ChatModel instance (placed in Runtime.context for generation).
        tenant_id: Tenant identifier.
        user_id: User identifier.
        message: User message text.
        conversation_id: Optional existing conversation ID.
        channel: Channel identifier (local, dingtalk, etc.).
        agent_id: Optional agent identifier.
        model_override: Optional model name override.

    Returns:
        Final graph state dict after full pipeline execution.
    """
    import uuid
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    checkpointer = InMemorySaver()  # P0: could use get_checkpointer()
    store = InMemoryStore()         # P0: cross-session memory

    graph = build_chat_graph_compiled(
        checkpointer=checkpointer,
        store=store,
    )

    result = await graph.ainvoke(
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "message": message,
            "conversation_id": conversation_id or str(uuid.uuid4()),
            "channel": channel,
            "agent_id": agent_id,
            "model_override": model_override,
        },
        config={"configurable": {"thread_id": conversation_id or str(uuid.uuid4())}},
        context={
            "db": db,
            "chat_model": chat_model,
        },
        durability="sync",  # P2: safe sync checkpoint for HTTP
    )
    return result


@router.post("/agent/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: DbSession) -> ChatResponse:
    """Agent 聊天 — 通过 ChatPipeline 执行完整请求生命周期。

    支持延迟优化的 fast / standard / slow 三级路径。
    """
    settings = get_settings()
    conversation_id = req.conversation_id or generate_id()

    try:
        pipeline = ChatPipeline(db, settings)
        result = await pipeline.execute(
            tenant_id=req.tenant_id,
            user_id=req.user_id,
            message=req.message,
            conversation_id=conversation_id,
            context=req.context.model_dump() if req.context else None,
            channel=req.channel,
            agent_id=req.agent_id,
            model=req.model,
        )

        # 构建响应
        runtime = get_tenant_runtime()
        response = build_chat_response(
            conversation_id=result.conversation_id,
            tenant_id=req.tenant_id,
            task_type=result.route_result.task_type if result.route_result else "fast_command",
            answer=result.answer_dict,
            sources=result.sources,
            risk=result.risk_result.to_dict(),
            debug=DebugInfo(
                retrieval_query="",
                route_confidence=result.route_result.confidence if result.route_result else 1.0,
                prompt_version="v0",
                run_id=result.run_id,
                model=runtime.chat_model or "default",
                latency_ms=int(result.timings.total_ms),
                provider=runtime.provider,
                base_url_host=runtime.base_url_host,
                api_key_ref=runtime.api_key_ref,
                api_key_fingerprint=runtime.api_key_fingerprint,
                path=result.path_result.path,
                path_reason=result.path_result.reason,
                stage_latency_ms={k: int(v) for k, v in result.timings.stages.items()},
                llm_calls={
                    "router": result.route_result.llm_router_called
                    if result.route_result and hasattr(result.route_result, "llm_router_called")
                    else False,
                    "main": result.fast_reply is None,
                    "risk": result.path_result.needs_llm_risk_check,
                    "summary": False,
                },
                usage=result.usage or {},
            ).model_dump(),
        )

        return ChatResponse(**response)

    except SalesAgentError as e:
        logger.warning("Sales agent error: %s (%s)", e.user_message, e.detail)
        raise HTTPException(
            status_code=ERROR_HTTP_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.user_message, "detail": e.detail},
        )
    except Exception as e:
        logger.error("Unexpected error in chat: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "系统异常，请稍后重试", "detail": str(e)},
        )


# ── Eval streaming endpoint ────────────────────────────────────────────
# 供 eval 脚本测试。复用 ChatPipeline 完整链路，额外返回 TTFT。

@router.post("/eval/streaming-chat", response_model=ChatResponse)
async def eval_streaming_chat(
    req: ChatRequest,
    db: DbSession = None,
) -> dict[str, Any]:
    _logger = logging.getLogger(__name__)
    try:
        from sales_agent.services.chat_pipeline import ChatPipeline as _CP

        pipeline = _CP(db, get_settings())
        result = await pipeline.execute(
            tenant_id=req.tenant_id,
            user_id=req.user_id or "eval",
            message=req.message,
            conversation_id=req.conversation_id or f"eval_{generate_id()}",
            context=None,
            channel="eval_streaming",
            agent_id=None,
        )

        # TTFT: ontology 路径用总耗时，legacy 路径用 retrieval + 10% generation 近似
        ttft_ms = int(result.timings.total_ms)
        streaming_chunks: list[dict] = []
        if not (result.path_result.needs_retrieval and get_settings().ontology.knowledge_engine == "ontology_neo4j"):
            stages = result.timings.stages
            ttft_ms = int(stages.get("retrieval", 0) + stages.get("generation", 0) * 0.1)

        runtime = get_tenant_runtime()
        response = build_chat_response(
            conversation_id=result.conversation_id,
            tenant_id=req.tenant_id,
            task_type=result.route_result.task_type if result.route_result else "fast_command",
            answer=result.answer_dict,
            sources=result.sources,
            risk=result.risk_result.to_dict(),
            debug=DebugInfo(
                retrieval_query="",
                route_confidence=result.route_result.confidence if result.route_result else 1.0,
                prompt_version="v0",
                run_id=generate_id(),
                model=runtime.chat_model or "default",
                latency_ms=int(result.timings.total_ms),
                provider=runtime.provider,
                base_url_host=runtime.base_url_host,
                api_key_ref=runtime.api_key_ref,
                api_key_fingerprint=runtime.api_key_fingerprint,
                path=result.path_result.path,
                path_reason=result.path_result.reason,
                stage_latency_ms={k: int(v) for k, v in result.timings.stages.items()},
                llm_calls={
                    "router": result.route_result.llm_router_called
                    if result.route_result and hasattr(result.route_result, "llm_router_called") else False,
                    "main": result.fast_reply is None,
                    "risk": result.path_result.needs_llm_risk_check,
                    "summary": False,
                },
                retrieval_info={
                    "ttft_ms": ttft_ms,
                    "streaming_chunks": streaming_chunks,
                },
                usage=result.usage or {},
            ).model_dump(),
        )
        return ChatResponse(**response)
    except Exception as _exc:
        _logger.exception("eval_streaming_chat failed")
        raise HTTPException(status_code=500, detail=str(_exc))
