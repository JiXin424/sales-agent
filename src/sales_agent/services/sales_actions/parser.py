"""销售动作 LLM 抽取器（parser seam）。

调用 :class:`~sales_agent.llm.base.ChatModel` 把用户消息抽取出
:class:`SalesActionExtraction`。镜像 :mod:`context_resolver` 的调用形态：
构造 messages → ``generate(response_format=json_object)`` →
:func:`parse_model_json`。**任何**调用/解析失败都不抛异常，而是返回一个
``intent="none"``、``confidence=0.0`` 的低置信结果，让调用方平滑退化为普通聊天。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sales_agent.prompts.sales_action_extractor_prompt import SALES_ACTION_EXTRACTOR_PROMPT
from sales_agent.services.sales_actions.contracts import SalesActionExtraction
from sales_agent.services.structured_router_output import parse_model_json

logger = logging.getLogger(__name__)


def _low_confidence_none(timezone: str) -> SalesActionExtraction:
    """LLM 调用/解析失败时的兜底抽取：低置信 none，调用方据此走普通聊天。"""
    return SalesActionExtraction(
        intent="none",
        explicit_create=False,
        title="",
        customer_name=None,
        action_type="other",
        time_text=None,
        scheduled_at=None,
        timezone=timezone,
        confidence=0.0,
        missing_fields=[],
        needs_clarification=False,
        clarification_question=None,
    )


def _build_messages(message: str, now: datetime, timezone: str) -> list[dict[str, str]]:
    """构造抽取器的 system + user 消息。"""
    user_content = (
        f"当前时间：{now.isoformat()}\n"
        f"时区：{timezone}\n"
        f"用户消息：{message}"
    )
    return [
        {"role": "system", "content": SALES_ACTION_EXTRACTOR_PROMPT},
        {"role": "user", "content": user_content},
    ]


async def parse_sales_action_request(
    message: str,
    chat_model: Any,
    now: datetime,
    timezone: str = "Asia/Shanghai",
) -> SalesActionExtraction:
    """用 *chat_model* 把 *message* 抽取为 :class:`SalesActionExtraction`。

    Parameters
    ----------
    message :
        用户最新输入消息。
    chat_model :
        支持 ``async generate(messages=..., response_format=...)`` 的模型实例
        （:class:`~sales_agent.llm.base.ChatModel`）。
    now :
        当前时刻（tz-aware），作为解析相对时间（「半小时后」）的基准。
    timezone :
        IANA 时区名，默认 ``Asia/Shanghai``。

    Returns
    -------
    SalesActionExtraction
        成功时为模型抽取结果；**任何**调用或解析失败时返回
        ``intent="none"``、``confidence=0.0`` 的兜底结果（不抛异常），
        以便调用方降级为普通聊天。
    """
    messages = _build_messages(message, now, timezone)
    try:
        response = await chat_model.generate(
            messages=messages,
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        return parse_model_json(response, SalesActionExtraction)
    except Exception:  # noqa: BLE001 — 契约要求：任何失败都降级，绝不抛给调用方
        logger.exception(
            "sales action extraction failed; degrading to low-confidence none "
            "(message=%r)", message
        )
        return _low_confidence_none(timezone)
