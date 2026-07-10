"""销售动作 LLM 抽取器（parser seam）。

调用 :class:`~sales_agent.llm.base.ChatModel` 把用户消息抽取出
:class:`SalesActionExtraction`。镜像 :mod:`context_resolver` 的调用形态：
构造 messages → ``generate(response_format=json_object)`` →
:func:`parse_model_json`。**任何**调用/解析失败都不抛异常，而是返回一个
``intent="none"``、``confidence=0.0`` 的低置信结果，让调用方平滑退化为普通聊天。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from sales_agent.llm.prompt_loader import get_prompt
from sales_agent.services.sales_actions.contracts import (
    OUTCOME_TAGS,
    OutcomeExtraction,
    SalesActionExtraction,
)
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
    """构造抽取器的 system + user 消息。

    ``now`` 统一换算到 ``timezone`` 声明的本地时区再写进 prompt。否则当
    ``now`` 是 UTC（容器默认时钟）而 prompt 又声明 ``Asia/Shanghai`` 时，LLM
    会把相对时间（「1分钟后」）按 +08:00 输出同一钟点数，换算回 UTC 反而早 8
    小时，被 :func:`validate_action_extraction` 判为 ``past_time`` 而拒绝建提醒。
    """
    try:
        base = now if now.tzinfo is not None else now.replace(tzinfo=dt_timezone.utc)
        now_local = base.astimezone(ZoneInfo(timezone))
    except Exception:  # 未知时区名等异常时退回原值，绝不因时区问题中断抽取
        now_local = now
    user_content = (
        f"当前时间：{now_local.isoformat()}\n"
        f"时区：{timezone}\n"
        f"用户消息：{message}"
    )
    return [
        {"role": "system", "content": get_prompt("task", "sales_action_extractor").template},
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
    try:
        messages = _build_messages(message, now, timezone)
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


OBSERVE_EXTRACTOR_PROMPT = """
你是一个销售结果分类器。根据用户的回复和动作的预期成功信号，分类动作结果。

分类标签（outcome_tag）：
- achieved: 用户明确达成目标（如"约到了""他已经确认了"）
- partial: 有推进但未完全达成（如"他回了消息但没给时间"）
- new_obstacle: 出现新障碍/异议（如"他说预算冻结""他们换技术负责人了"）
- no_response: 用户没给出结果信息（如"好的""知道了"）

返回 JSON: {"outcome_tag": "<标签>", "outcome_note": "语义摘要", "met_signal": true/false, "confidence": 0.0-1.0}
"""


def _fallback_outcome(reply: str, *, confidence: float = 0.3) -> OutcomeExtraction:
    """Keyword-heuristic fallback when LLM parse fails."""
    positive = any(w in reply for w in ["约到", "确认", "完成", "搞定", "可以", "没问题", "同意了"])
    obstacle = any(w in reply for w in ["预算", "冻结", "异议", "换人", "不接", "拒绝", "暂停", "再说"])
    partial = any(w in reply for w in ["回复", "消息", "微信", "问了", "还没", "等"])

    if obstacle:
        tag = "new_obstacle"
    elif positive:
        tag = "achieved"
    elif partial:
        tag = "partial"
    else:
        tag = "no_response"

    return OutcomeExtraction(
        outcome_tag=tag,
        outcome_note=reply[:256],
        met_signal=(tag == "achieved"),
        confidence=confidence,
        parse_failed=True,
    )


async def parse_observe_outcome(
    reply: str,
    success_criteria: str,
    chat_model: Any,
) -> OutcomeExtraction:
    """Parse a user's reply into a structured outcome against the given success_criteria.

    Retries once on parse failure; falls back to keyword-heuristic on both-fail.
    """
    import json
    import re
    from json_repair import repair_json

    messages = [
        {"role": "system", "content": OBSERVE_EXTRACTOR_PROMPT},
        {"role": "user", "content": (
            f"成功信号：{success_criteria}\n"
            f"用户回复：{reply}\n"
            f"请分类此动作结果。"
        )},
    ]

    for attempt in range(2):
        try:
            raw = await chat_model.generate(
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=300,
                temperature=0.0,
            )
            # Parse JSON using the same codec as parse_model_json but without
            # requiring a full Pydantic schema (we validate fields manually).
            text = raw.strip()
            fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
            if fenced:
                text = fenced.group(1)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = repair_json(text, return_objects=True)

            if (
                isinstance(data, dict)
                and data.get("outcome_tag") in OUTCOME_TAGS
            ):
                return OutcomeExtraction(
                    outcome_tag=data["outcome_tag"],
                    outcome_note=data.get("outcome_note", reply[:256]),
                    met_signal=data.get("met_signal", data["outcome_tag"] == "achieved"),
                    confidence=data.get("confidence", 0.8 if attempt == 0 else 0.5),
                )
        except Exception:
            continue

    return _fallback_outcome(reply)
