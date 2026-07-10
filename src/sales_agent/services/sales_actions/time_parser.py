"""抽取结果校验 + 时间解析（time_parser）。

把 :class:`SalesActionExtraction` 校验为对下游可执行的
:class:`SalesActionDecision`：低置信度 / 缺标题 / 过去时间 / LLM 显式澄清
均降级为 ``clarify``；只有明确的未来时间 + 标题 + explicit_create 才生成
``create`` 决策；``suggest_action`` 只要有标题即浮出 ``suggest`` 供用户确认
（时间可缺失，由用户确认时再敲定）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sales_agent.services.sales_actions.contracts import (
    SalesActionDecision,
    SalesActionExtraction,
)

logger = logging.getLogger(__name__)

# 置信度阈值：低于此值不信任 LLM 抽取，直接请求澄清。
CONFIDENCE_THRESHOLD = 0.75

# 创建提醒回执的固定时间格式：YYYY-MM-DD HH:mm
_CREATE_TEXT_FORMAT = "已创建提醒：{when}，提醒你{title}。"


def _attach_tz(dt: datetime, timezone_str: str) -> datetime:
    """确保 *dt* 是 tz-aware；naive 时挂上 *timezone_str*。"""
    if dt.tzinfo is not None:
        return dt
    try:
        return dt.replace(tzinfo=ZoneInfo(timezone_str))
    except Exception:
        # 兜底：解析不了 IANA 名就用 +08:00（销售场景默认东亚）
        logger.warning("unknown timezone %r, falling back to UTC+8", timezone_str)
        return dt.replace(tzinfo=timezone(timedelta(hours=8)))


def parse_scheduled_at(value: str | None, timezone_str: str = "Asia/Shanghai") -> datetime | None:
    """把 ISO-8601 字符串解析为 tz-aware datetime；失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.warning("unparseable scheduled_at=%r", value)
        return None
    return _attach_tz(dt, timezone_str)


def _clarify(extraction: SalesActionExtraction, *, response_text: str, reason_code: str) -> SalesActionDecision:
    """构造一个 ``clarify`` 决策，携带 title/customer/timezone 供下游追问复用。"""
    return SalesActionDecision(
        action="clarify",
        title=extraction.title,
        customer_name=extraction.customer_name,
        action_type=extraction.action_type,
        timezone=extraction.timezone,
        response_text=response_text,
        reason_code=reason_code,
    )


def validate_action_extraction(
    extraction: SalesActionExtraction,
    *,
    now: datetime,
) -> SalesActionDecision:
    """把 LLM 抽取结果校验为下游可执行的 :class:`SalesActionDecision`。

    校验优先级（前者先判）：

    1. ``intent == none`` → ``ignore``
    2. 置信度低于 :data:`CONFIDENCE_THRESHOLD` → ``clarify``
    3. LLM 显式 ``needs_clarification`` → ``clarify``，回显其问题
    4. create/suggest 路径缺标题 → ``clarify``
    5. ``suggest_action`` 有标题 → ``suggest``（时间可能缺失/模糊/过去，
       由用户确认动作时再敲定；spec: Agent-inferred action items require
       user confirmation）
    6. ``create_action`` 缺/无法解析时间 → ``clarify``
    7. ``create_action`` 时间在过去 → ``clarify``（提示「过去」）
    8. ``explicit_create`` → ``create``（回执格式 ``已创建提醒：…``）
    9. 非 explicit 的 create_action 可执行计划 → ``suggest``

    complete/cancel/snooze/list 等非建提醒意图不在本函数的建提醒决策范围内，
    返回 ``ignore`` 并交由 service 层（Task 3）处理。
    """
    # 1. 非动作意图
    if extraction.intent == "none":
        return SalesActionDecision(
            action="ignore",
            title=extraction.title,
            customer_name=extraction.customer_name,
            action_type=extraction.action_type,
            timezone=extraction.timezone,
            reason_code="not_an_action",
        )

    # 2. 低置信度：不信任抽取，直接澄清
    if extraction.confidence < CONFIDENCE_THRESHOLD:
        return _clarify(
            extraction,
            response_text="我还不太确定你的意思，能再说具体一点吗？",
            reason_code="low_confidence",
        )

    # 3. LLM 显式要求澄清 —— 原样回显 clarification_question
    if extraction.needs_clarification:
        question = extraction.clarification_question or "请补充更多细节。"
        return _clarify(
            extraction,
            response_text=question,
            reason_code="llm_needs_clarification",
        )

    # create / suggest 路径都需要标题；但只有 create 需要敲定时间
    if extraction.intent in ("create_action", "suggest_action"):
        # 4. 缺标题（create 无标题无法建提醒；suggest 无标题也无从建议）
        if not extraction.title or "title" in extraction.missing_fields:
            return _clarify(
                extraction,
                response_text="要我提醒你做什么？请说一下具体的事。",
                reason_code="missing_title",
            )

        # 5. suggest 只要有标题即可浮出供用户确认；时间可能缺失/模糊/过去，
        #    由用户确认动作时再敲定（spec: Agent-inferred action items require
        #    user confirmation）。SalesActionDecision.scheduled_at 类型为 datetime | None。
        if extraction.intent == "suggest_action":
            return SalesActionDecision(
                action="suggest",
                title=extraction.title,
                customer_name=extraction.customer_name,
                action_type=extraction.action_type,
                scheduled_at=parse_scheduled_at(extraction.scheduled_at, extraction.timezone),
                timezone=extraction.timezone,
                response_text=f"建议下一步：{extraction.title}",
                reason_code="suggested",
            )

        # 以下时间校验仅对 create_action（explicit creation）生效
        # 6. 缺时间
        if not extraction.scheduled_at or "scheduled_at" in extraction.missing_fields:
            return _clarify(
                extraction,
                response_text="你希望几点提醒你？",
                reason_code="missing_time",
            )

        scheduled = parse_scheduled_at(extraction.scheduled_at, extraction.timezone)
        # 6b. 时间无法解析
        if scheduled is None:
            return _clarify(
                extraction,
                response_text="我没听懂这个时间，你想几点提醒？",
                reason_code="unparseable_time",
            )

        # 7. 过去时间
        if scheduled <= now:
            return _clarify(
                extraction,
                response_text="那个时间已经过去了，要改成什么时候提醒你？",
                reason_code="past_time",
            )

        # 8. explicit create → 建提醒
        if extraction.explicit_create:
            when = scheduled.strftime("%Y-%m-%d %H:%M")
            return SalesActionDecision(
                action="create",
                title=extraction.title,
                customer_name=extraction.customer_name,
                action_type=extraction.action_type,
                scheduled_at=scheduled,
                timezone=extraction.timezone,
                response_text=_CREATE_TEXT_FORMAT.format(when=when, title=extraction.title),
                reason_code="created",
            )

        # 9. 非 explicit 的 create_action 可执行计划 → 建议
        return SalesActionDecision(
            action="suggest",
            title=extraction.title,
            customer_name=extraction.customer_name,
            action_type=extraction.action_type,
            scheduled_at=scheduled,
            timezone=extraction.timezone,
            response_text=f"建议下一步：{extraction.title}",
            reason_code="suggested",
        )

    # complete/cancel/snooze/list：非建提醒决策，交给 service 层
    return SalesActionDecision(
        action="ignore",
        title=extraction.title,
        customer_name=extraction.customer_name,
        action_type=extraction.action_type,
        timezone=extraction.timezone,
        reason_code="deferred_to_service",
    )
