"""钉钉投递日志 — 记录出站消息发送状态。"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.integrations.dingtalk.models import DingTalkOutboundMessage
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


class DingTalkDeliveryLogger:
    """钉钉投递日志记录器。"""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def log_sent(
        self,
        tenant_id: str,
        corp_id: str,
        dingtalk_user_id: str,
        conversation_id: str,
        text: str,
        agent_conversation_id: str | None = None,
        status: str = "sent",
    ) -> DingTalkOutboundMessage:
        """记录成功发送的消息。"""
        msg = DingTalkOutboundMessage(
            id=generate_id(),
            tenant_id=tenant_id,
            corp_id=corp_id,
            dingtalk_user_id=dingtalk_user_id,
            conversation_id=conversation_id,
            agent_conversation_id=agent_conversation_id,
            text=text,
            status=status,
            retry_count=0,
        )
        self._db.add(msg)
        await self._db.flush()
        return msg

    async def log_failed(
        self,
        tenant_id: str,
        corp_id: str,
        dingtalk_user_id: str,
        conversation_id: str,
        text: str,
        error: str,
        retry_count: int = 0,
    ) -> DingTalkOutboundMessage:
        """记录发送失败的消息。"""
        msg = DingTalkOutboundMessage(
            id=generate_id(),
            tenant_id=tenant_id,
            corp_id=corp_id,
            dingtalk_user_id=dingtalk_user_id,
            conversation_id=conversation_id,
            text=text,
            status="failed",
            retry_count=retry_count,
            error_json=json.dumps({"error": error}, ensure_ascii=False),
        )
        self._db.add(msg)
        await self._db.flush()
        return msg

    async def update_status(
        self,
        outbound_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """更新出站消息状态。"""
        from sqlalchemy import select

        stmt = select(DingTalkOutboundMessage).where(
            DingTalkOutboundMessage.id == outbound_id,
        )
        result = await self._db.execute(stmt)
        msg = result.scalar_one_or_none()
        if msg:
            msg.status = status
            if error:
                msg.error_json = json.dumps({"error": error}, ensure_ascii=False)
            await self._db.flush()
