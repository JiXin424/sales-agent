"""会话查看路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.models.conversation import Conversation, ConversationMessage

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, db: DbSession):
    """查看会话详情和消息。"""
    # 查询会话
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()

    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 查询消息列表
    msg_stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    msg_result = await db.execute(msg_stmt)
    messages = msg_result.scalars().all()

    import json

    return {
        "conversation_id": conv.id,
        "tenant_id": conv.tenant_id,
        "user_id": conv.user_id,
        "channel": conv.channel,
        "task_type": conv.task_type,
        "task_confidence": conv.task_confidence,
        "status": conv.status,
        "risk": json.loads(conv.risk_json) if conv.risk_json else None,
        "sources": json.loads(conv.sources_json) if conv.sources_json else None,
        "created_at": conv.created_at,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }
