"""钉钉互动卡片按钮回调采集（点赞 / 点踩 / 问题反馈多选等）。

capture-first：注册到 Stream 长连接的卡片回调 topic
（``/v1.0/card/instances/callback``），把真实点击的原始载荷原样落库到
:class:`DingTalkCardCallbackEvent`。「原始载荷 → :class:`Feedback`」的映射
留待后续里程碑基于真实样本处理。

设计要点（全租户优雅降级）：
- :meth:`SalesAgentCardCallbackHandler.process` 全程 try/except，任何异常
  只记录、绝不外抛，恒返回 ``AckMessage.STATUS_OK`` —— 卡片回调不得把
  Stream 主循环带崩。
- 不发卡片的租户永远收不到该 topic 回调，注册本身零副作用。
- 载荷解析用 :meth:`CardCallbackMessage.from_dict`，缺字段/畸形也不崩。
"""

from __future__ import annotations

import json
import logging

import dingtalk_stream

from sales_agent.integrations.dingtalk.models import DingTalkCardCallbackEvent

logger = logging.getLogger(__name__)


async def capture_card_callback(
    db,
    msg: "dingtalk_stream.CardCallbackMessage",
    tenant_id: str,
    raw: dict,
) -> DingTalkCardCallbackEvent:
    """把一条钉钉卡片回调原样落库为 :class:`DingTalkCardCallbackEvent`。

    纯函数（注入 db session），便于单测。不 commit —— 由调用方管理事务。

    Args:
        db: AsyncSession
        msg: 已解析的 CardCallbackMessage
        tenant_id: 当前 Stream 容器的租户
        raw: 完整原始 callback.data（兜底存档）

    Returns:
        已 add 到 session 的事件对象（未 flush/commit）。
    """
    event = DingTalkCardCallbackEvent(
        tenant_id=tenant_id,
        dingtalk_user_id=getattr(msg, "user_id", "") or "",
        card_instance_id=getattr(msg, "card_instance_id", None) or None,
        corp_id=getattr(msg, "corp_id", None) or None,
        space_id=getattr(msg, "space_id", None) or None,
        content_json=json.dumps(getattr(msg, "content", {}) or {}, ensure_ascii=False),
        extension_json=json.dumps(getattr(msg, "extension", {}) or {}, ensure_ascii=False),
        raw_json=json.dumps(raw if isinstance(raw, dict) else {}, ensure_ascii=False),
    )
    db.add(event)
    return event


class SalesAgentCardCallbackHandler(dingtalk_stream.CallbackHandler):
    """钉钉互动卡片按钮回调处理器（采集落库，恒 ACK）。"""

    def __init__(self, tenant_id: str) -> None:
        super().__init__()
        self._tenant_id = tenant_id

    async def process(self, callback: "dingtalk_stream.CallbackMessage") -> tuple[int, str]:
        """采集卡片回调载荷。任何异常都不外抛，恒返回 STATUS_OK。"""
        try:
            raw = callback.data if isinstance(callback.data, dict) else {}
            msg = dingtalk_stream.CardCallbackMessage.from_dict(raw)
        except Exception:
            logger.exception("card callback parse failed, acking anyway")
            return dingtalk_stream.AckMessage.STATUS_OK, "parse_error"

        try:
            from sales_agent.core.database import get_session_factory

            factory = get_session_factory()
            async with factory() as db:
                await capture_card_callback(db, msg, self._tenant_id, raw)
                await db.commit()
            logger.info(
                "card callback captured: tenant=%s user=%s card=%s content_keys=%s",
                self._tenant_id,
                getattr(msg, "user_id", ""),
                getattr(msg, "card_instance_id", ""),
                list((getattr(msg, "content", {}) or {}).keys()),
            )
        except Exception:
            logger.exception(
                "card callback persist failed (tenant=%s user=%s), acking anyway",
                self._tenant_id,
                getattr(msg, "user_id", ""),
            )

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
