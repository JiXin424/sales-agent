"""会话日志记录服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.conversation import Conversation, ConversationMessage, RetrievalLog
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


async def log_conversation(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    channel: str,
    conversation_id: str,
    message: str,
    normalized_message: str | None = None,
    task_type: str | None = None,
    task_confidence: float | None = None,
    answer: str | None = None,
    answer_dict: dict | None = None,
    risk_dict: dict | None = None,
    sources: list[dict] | None = None,
    model_config: dict | None = None,
    status: str = "completed",
    error: dict | None = None,
    stage_latency_ms: dict[str, float] | None = None,
    llm_calls: dict[str, bool] | None = None,
    path: str | None = None,
    path_reason: str | None = None,
    retrieval_info: dict | None = None,
    stage: str | None = None,
    agent_id: str | None = None,
) -> Conversation:
    """记录一次完整的会话。

    写入 conversations 和 conversation_messages 表。
    日志写入失败不影响主流程。
    """
    try:
        answer_text = answer or (json.dumps(answer_dict, ensure_ascii=False) if answer_dict else None)

        # 构建扩展的 model_config（加入延迟优化调试信息）
        extended_model_config = dict(model_config) if model_config else {}
        if stage_latency_ms:
            extended_model_config["stage_latency_ms"] = stage_latency_ms
        if llm_calls:
            extended_model_config["llm_calls"] = llm_calls
        if path:
            extended_model_config["path"] = path
        if path_reason:
            extended_model_config["path_reason"] = path_reason
        if retrieval_info:
            extended_model_config["retrieval_info"] = retrieval_info

        # 检查 conversation 是否已存在（同一 conversation_id 可能被多次使用）
        from sqlalchemy import select
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        result = await db.execute(stmt)
        existing_conv = result.scalar_one_or_none()

        if existing_conv:
            # 已存在：更新最新消息和状态，不重复创建
            if agent_id:
                existing_conv.agent_id = agent_id
            existing_conv.message = message
            existing_conv.normalized_message = normalized_message
            existing_conv.task_type = task_type
            existing_conv.task_confidence = task_confidence
            existing_conv.answer = answer_text
            existing_conv.risk_json = json.dumps(risk_dict, ensure_ascii=False) if risk_dict else existing_conv.risk_json
            existing_conv.sources_json = json.dumps(sources, ensure_ascii=False) if sources else existing_conv.sources_json
            existing_conv.model_config_json = json.dumps(extended_model_config, ensure_ascii=False) if extended_model_config else existing_conv.model_config_json
            existing_conv.status = status
            existing_conv.error_json = json.dumps(error, ensure_ascii=False) if error else existing_conv.error_json
            if stage:
                existing_conv.stage = stage
            conv = existing_conv
        else:
            # 不存在：创建新 conversation
            conv = Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                channel=channel,
                message=message,
                normalized_message=normalized_message,
                task_type=task_type,
                task_confidence=task_confidence,
                answer=answer_text,
                risk_json=json.dumps(risk_dict, ensure_ascii=False) if risk_dict else None,
                sources_json=json.dumps(sources, ensure_ascii=False) if sources else None,
                model_config_json=json.dumps(extended_model_config, ensure_ascii=False) if extended_model_config else None,
                status=status,
                error_json=json.dumps(error, ensure_ascii=False) if error else None,
                stage=stage,
            )
            db.add(conv)

        # 同时记录消息
        await log_message(
            db,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            role="user",
            content=message,
        )
        if answer_text:
            await log_message(
                db,
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                role="assistant",
                content=answer_text,
            )

        await db.flush()
        return conv

    except Exception as e:
        logger.error("Failed to log conversation: %s", e, exc_info=True)
        # 日志写入失败不影响用户得到回答
        return None


async def log_message(
    db: AsyncSession,
    *,
    conversation_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
    agent_id: str | None = None,
) -> ConversationMessage:
    """记录一条消息。"""
    msg = ConversationMessage(
        id=generate_id(),
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        role=role,
        content=content,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )
    db.add(msg)
    return msg


async def log_retrieval(
    db: AsyncSession,
    *,
    conversation_id: str,
    tenant_id: str,
    query: str,
    sources: list[dict],
    agent_id: str | None = None,
) -> RetrievalLog:
    """记录检索日志。"""
    log = RetrievalLog(
        id=generate_id(),
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        query=query,
        sources_json=json.dumps(sources, ensure_ascii=False),
    )
    db.add(log)
    return log
