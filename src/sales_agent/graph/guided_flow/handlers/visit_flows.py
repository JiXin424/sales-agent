"""Guided flow handlers: visit preparation (访前准备) and post-visit review (访后复盘).

Each flow collects three pieces of information one question per turn, then
generates a structured card via LLM (with deterministic fallback when the model
is unavailable or raises).
"""

from __future__ import annotations

import logging
from typing import Any

from sales_agent.graph.guided_flow.types import FlowAdvance, FlowServices, FlowStart
from sales_agent.services.agent_executor import execute_agent
from sales_agent.llm.prompt_loader import get_prompt

logger = logging.getLogger(__name__)


# ============================================================
# 访前准备 — 3 问：客户 → 现状 → 目标 → 访前作战卡
# ============================================================


def start_visit_preparation() -> FlowStart:
    """Start the visit-preparation flow.

    Returns:
        FlowStart with stage ``"customer"``, empty payload, and the first question.
    """
    return FlowStart("customer", {}, "你这次要见谁？请说一下客户、组织或对方角色。")


async def advance_visit_preparation(
    stage: str,
    payload: dict[str, Any],
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    """Advance the visit-preparation flow by one turn.

    Transitions:
        ``customer`` → ``situation`` → ``goal`` → ``completed``

    At the terminal transition the collected facts are rendered into a
    "访前作战卡" via ``_generate_card`` with deterministic fallback.
    """
    text = (text or "").strip()
    new_payload = {**payload}

    if stage == "customer":
        new_payload["customer"] = text
        return FlowAdvance(
            "situation", new_payload, "客户现在大概什么情况？请简单介绍一下背景。", False,
        )

    if stage == "situation":
        new_payload["situation"] = text
        return FlowAdvance(
            "goal", new_payload, "这次你最想推进到哪一步？", False,
        )

    # Terminal: goal → generate card
    new_payload["goal"] = text
    combined = (
        f"客户对象：{new_payload['customer']}\n"
        f"客户现状：{new_payload['situation']}\n"
        f"本次沟通目标：{new_payload['goal']}"
    )
    fallback = _visit_fallback(new_payload)
    try:
        card = await _generate_card("visit_preparation", combined, fallback, services)
    except Exception:  # noqa: BLE001
        logger.exception("visit_preparation card generation failed, using fallback")
        card = fallback
    return FlowAdvance("completed", new_payload, card, True)


# ============================================================
# 访后复盘 — 3 问：客户表达 → 态度 → 下一步 → 机会推进卡
# ============================================================


def start_post_visit() -> FlowStart:
    """Start the post-visit review flow.

    Returns:
        FlowStart with stage ``"customer_words"``, empty payload, and the first
        question.
    """
    return FlowStart(
        "customer_words",
        {},
        "刚才客户主要说了什么？请尽量保留客户的原话或明确事实。",
    )


async def advance_post_visit(
    stage: str,
    payload: dict[str, Any],
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    """Advance the post-visit review flow by one turn.

    Transitions:
        ``customer_words`` → ``attitude`` → ``next_step`` → ``completed``

    At the terminal transition the collected facts are rendered into a
    "访后机会推进卡" via ``_generate_card`` with deterministic fallback.
    """
    text = (text or "").strip()
    new_payload = {**payload}

    if stage == "customer_words":
        new_payload["customer_words"] = text
        return FlowAdvance(
            "attitude", new_payload, "客户现在是什么态度？", False,
        )

    if stage == "attitude":
        new_payload["attitude"] = text
        return FlowAdvance(
            "next_step", new_payload, "你们有没有约定下一步？", False,
        )

    # Terminal: next_step → generate card
    new_payload["next_step"] = text
    combined = (
        f"客户表达：{new_payload['customer_words']}\n"
        f"客户态度：{new_payload['attitude']}\n"
        f"下一步约定：{new_payload['next_step']}"
    )
    fallback = _post_visit_fallback(new_payload)
    try:
        card = await _generate_card("post_visit_review", combined, fallback, services)
    except Exception:  # noqa: BLE001
        logger.exception("post_visit_review card generation failed, using fallback")
        card = fallback
    return FlowAdvance("completed", new_payload, card, True)


# ============================================================
# 确定性 fallback 出卡（无模型 / 模型失败时使用）
# ============================================================


def _visit_fallback(payload: dict[str, Any]) -> str:
    """Build a deterministic 访前作战卡 from collected payload."""
    return (
        "## 访前作战卡\n\n"
        f"**客户对象**：{payload['customer']}\n\n"
        f"**客户现状**：{payload['situation']}\n\n"
        f"**本次沟通目标**：{payload['goal']}\n\n"
        "**建议动作**：围绕目标确认客户当前优先级、关键顾虑和可接受的下一步。"
    )


def _post_visit_fallback(payload: dict[str, Any]) -> str:
    """Build a deterministic 访后机会推进卡 from collected payload."""
    return (
        "## 访后机会推进卡\n\n"
        f"**客户表达**：{payload['customer_words']}\n\n"
        f"**客户态度**：{payload['attitude']}\n\n"
        f"**下一步约定**：{payload['next_step']}\n\n"
        "**复盘动作**：按约定时间跟进，并验证客户是否完成了承诺动作。"
    )


# ============================================================
# LLM 出卡（失败时以 fallback 字符串返回）
# ============================================================


async def _generate_card(
    flow_id: str,
    message: str,
    fallback: str,
    services: FlowServices,
) -> str:
    """Generate a structured card via LLM, returning *fallback* on any failure.

    When ``services.chat_model`` is ``None`` the fallback is returned immediately
    (no attempt).  Otherwise ``execute_agent`` is called; if the result contains
    neither ``summary`` nor ``sections`` the fallback is returned.
    """
    if services.chat_model is None:
        return fallback

    prompt_text: str | None = None
    system_prompt_text: str | None = None
    if services.db is not None:
        try:
            prompt_text = get_prompt("task", flow_id).template
            system_prompt_text = get_prompt("system", "system_constraint").template
        except Exception:
            logger.warning("Prompt resolution failed", exc_info=True)

    answer = await execute_agent(
        chat_model=services.chat_model,
        task_type=flow_id,
        message=message,
        context={},
        retrieval_result=None,
        history_messages=[],
        tenant_style={},
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )

    summary = answer.get("summary", "")
    sections = answer.get("sections", [])
    rendered_sections = "\n\n".join(
        f"## {section.get('title', '')}\n{section.get('content', '')}"
        for section in sections
        if section.get("content")
    )
    return "\n\n".join(part for part in (summary, rendered_sections) if part) or fallback


__all__ = [
    "advance_post_visit",
    "advance_visit_preparation",
    "start_post_visit",
    "start_visit_preparation",
]
