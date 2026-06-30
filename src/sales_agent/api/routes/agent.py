"""Agent Chat 路由 — 委托 ChatPipeline 执行。"""

from __future__ import annotations

import logging

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
from sales_agent.models.base import generate_id
from sales_agent.services.chat_pipeline import ChatPipeline
from sales_agent.services.response_formatter import build_chat_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


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
