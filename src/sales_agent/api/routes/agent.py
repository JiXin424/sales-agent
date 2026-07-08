"""Agent Chat 路由 — 使用 LangGraph StateGraph 执行。

统一使用 Graph 路径（与钉钉 Stream 同一管线）。
"""

from __future__ import annotations

import logging
import time
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
from sales_agent.models.base import generate_id
from sales_agent.services.online_conversation import invoke_online_turn
from sales_agent.services.response_formatter import build_chat_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


async def _execute_via_graph(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    message: str,
    conversation_id: str | None,
    channel: str,
    agent_id: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Execute chat via the Unified Online Conversation Graph.

    Delegates to ``invoke_online_turn`` instead of compiling a per-request
    Chat Graph.  Keeps the same public signature for backward compatibility.

    Args:
        db: Database session (placed in Runtime.context for graph nodes).
        tenant_id: Tenant identifier.
        user_id: User identifier.
        message: User message text.
        conversation_id: Optional existing conversation ID.
        channel: Channel identifier (local, dingtalk, etc.).
        agent_id: Optional agent identifier.
        model_override: Optional model name override (deprecated, models
            resolved by runtime).

    Returns:
        Final graph state dict after full pipeline execution, with fields
        mapped to the shape expected by the chat and eval route handlers.
    """
    result = await invoke_online_turn(
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        session_user_id=user_id,  # HTTP: use user_id as session_user_id
        channel=channel,
        conversation_id=conversation_id or generate_id(),
        message=message,
        entry_action=None,  # HTTP: no DingTalk entry_action
        event_id=None,  # HTTP: no DingTalk event_id
        chat_model=None,  # resolved by TenantResolver at runtime
        embedding_model=None,
    )

    # Derive path and task_type from the online state
    response_kind = result.get("response_kind", "chat")
    is_guided = response_kind in (
        "flow_question", "flow_completed", "flow_cancelled", "flow_retry",
    )

    answer_dict = result.get("answer_dict") or result.get("final_answer") or {
        "summary": "", "sections": []
    }

    if is_guided:
        path = "guided_flow"
        task_type = result.get("completed_flow") or result.get("active_flow") or "guided_flow"
    else:
        path = result.get("path", "standard")
        task_type = result.get("task_type", "general_sales_coaching")

    return {
        "answer_dict": answer_dict,
        "task_type": task_type,
        "path": path,
        "path_reason": result.get("path_reason", ""),
        "sources": result.get("sources") or result.get("final_sources") or [],
        "risk_result": result.get("risk_result") or {},
        "retrieval_info": result.get("retrieval_info") or {},
        "usage": result.get("usage") or {},
        "conversation_id": result.get("conversation_id", conversation_id) or generate_id(),
        "response_kind": response_kind,
        "route_confidence": result.get("route_confidence") or 1.0,
        "run_id": result.get("run_id"),
    }


@router.post("/agent/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: DbSession) -> ChatResponse:
    """Agent 聊天 — 通过 LangGraph StateGraph 执行（与钉钉 Stream 同一管线）。"""
    settings = get_settings()
    conversation_id = req.conversation_id or generate_id()
    start_time = time.time()

    try:
        result = await _execute_via_graph(
            db=db,
            tenant_id=req.tenant_id,
            user_id=req.user_id,
            message=req.message,
            conversation_id=conversation_id,
            channel=req.channel,
            agent_id=req.agent_id,
            model_override=req.model,
        )

        total_ms = int((time.time() - start_time) * 1000)
        runtime = get_tenant_runtime()

        # 从 graph state 提取字段
        task_type = result.get("task_type", "general_sales_coaching")
        answer_dict = result.get("answer_dict") or result.get("final_answer") or {
            "summary": "", "sections": []
        }
        sources = result.get("sources") or result.get("final_sources") or []
        risk_result = result.get("risk_result") or {}
        path = result.get("path", "standard")
        path_reason = result.get("path_reason", "")
        retrieval_info = result.get("retrieval_info") or {}
        usage = result.get("usage") or {}

        response = build_chat_response(
            conversation_id=result.get("conversation_id", conversation_id),
            tenant_id=req.tenant_id,
            task_type=task_type,
            answer=answer_dict,
            sources=sources,
            risk=risk_result,
            debug=DebugInfo(
                retrieval_query="",
                route_confidence=result.get("route_confidence") or 1.0,
                prompt_version="v0",
                run_id=result.get("run_id") or generate_id(),
                model=runtime.chat_model or "default",
                latency_ms=total_ms,
                provider=runtime.provider,
                base_url_host=runtime.base_url_host,
                api_key_ref=runtime.api_key_ref,
                api_key_fingerprint=runtime.api_key_fingerprint,
                path=path,
                path_reason=path_reason,
                stage_latency_ms={},
                llm_calls={
                    "router": bool(result.get("route_confidence")),
                    "main": bool(answer_dict.get("summary")),
                    "risk": retrieval_info.get("risk_checked", False),
                    "summary": False,
                },
                usage=usage,
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

@router.post("/eval/streaming-chat", response_model=ChatResponse)
async def eval_streaming_chat(
    req: ChatRequest,
    db: DbSession = None,
) -> dict[str, Any]:
    """Eval 用的同步 chat 端点（名义 "streaming"，实际非流式）。

    走 Online Graph 完整执行后一次性返回 ChatResponse——并不产生真实流式
    chunk。因此 ``debug.retrieval_info`` 里 ``ttft_ms`` 等于总延迟（首字≈末字），
    ``streaming_chunks`` 恒为空。eval 用它模拟「整段返回」的调用形态。
    """
    _logger = logging.getLogger(__name__)
    start_time = time.time()
    try:
        result = await _execute_via_graph(
            db=db,
            tenant_id=req.tenant_id,
            user_id=req.user_id or "eval",
            message=req.message,
            conversation_id=req.conversation_id or f"eval_{generate_id()}",
            channel="eval_streaming",
            agent_id=None,
        )

        total_ms = int((time.time() - start_time) * 1000)
        runtime = get_tenant_runtime()

        task_type = result.get("task_type", "general_sales_coaching")
        answer_dict = result.get("answer_dict") or result.get("final_answer") or {
            "summary": "", "sections": []
        }
        sources = result.get("sources") or result.get("final_sources") or []
        risk_result = result.get("risk_result") or {}

        ttft_ms = total_ms
        streaming_chunks: list[dict] = []

        response = build_chat_response(
            conversation_id=result.get("conversation_id", req.conversation_id or ""),
            tenant_id=req.tenant_id,
            task_type=task_type,
            answer=answer_dict,
            sources=sources,
            risk=risk_result,
            debug=DebugInfo(
                retrieval_query="",
                route_confidence=result.get("route_confidence") or 1.0,
                prompt_version="v0",
                run_id=generate_id(),
                model=runtime.chat_model or "default",
                latency_ms=total_ms,
                provider=runtime.provider,
                base_url_host=runtime.base_url_host,
                api_key_ref=runtime.api_key_ref,
                api_key_fingerprint=runtime.api_key_fingerprint,
                path=result.get("path", "standard"),
                path_reason=result.get("path_reason", ""),
                stage_latency_ms={},
                llm_calls={
                    "router": bool(result.get("route_confidence")),
                    "main": bool(answer_dict.get("summary")),
                    "risk": False,
                    "summary": False,
                },
                retrieval_info={
                    "ttft_ms": ttft_ms,
                    "streaming_chunks": streaming_chunks,
                },
                usage=result.get("usage") or {},
            ).model_dump(),
        )
        return ChatResponse(**response)
    except Exception as _exc:
        _logger.exception("eval_streaming_chat failed")
        raise HTTPException(status_code=500, detail=str(_exc))
