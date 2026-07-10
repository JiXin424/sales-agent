"""Guided flow handlers: small-win appreciation / sales-block breakthrough.

Extracted from ``sales_agent.coach.quick_session`` — pure business logic only,
no DB session coupling. Exposes 4 adapter functions for the guided flow graph.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sales_agent.llm.call_params import get_call_params
from sales_agent.prompts.coach_quick import (
    SB_CARD_TEMPLATE,
    SB_SPLIT_TEMPLATE,
    SB_SYSTEM,
    SW_CARD_TEMPLATE,
    SW_SYSTEM,
)

logger = logging.getLogger(__name__)


# ============================================================
# 小赢欣赏 — 4 步：小赢 → 优势 → 感谢 → 能量句 → 小赢卡
# ============================================================

SMALL_WIN_OPENING = (
    "我们用 3 分钟做一次“小赢欣赏”。不是总结成绩，也不是批评自己，"
    "只是看见今天真实发生的一个小进展。哪怕很小，也算。\n\n"
    "今天你有哪一个小小的进展或小赢？"
)
SMALL_WIN_QUESTIONS = {
    "strength": "这个小赢背后，你觉得自己哪一点做得不错？",
    "gratitude": "关于这个小赢，你想感谢什么？",
    "energy": "如果把今天这个小赢变成一句话，你想怎么鼓励自己？也可以说“AI 代写”。",
}
_SMALL_WIN_FORBIDDEN = ("明天行动", "下一步计划", "待办事项")


def _sw_start() -> tuple[str, dict[str, Any], str]:
    return "small_win", {}, SMALL_WIN_OPENING


async def _sw_advance(
    chat_model: Any, stage: str, payload: dict[str, Any], text: str,
    prompts: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any], str, bool]:
    text = _clean(text)
    if stage == "small_win":
        if _sw_needs_clarify(text) and not payload.get("clarified"):
            payload = {**payload, "clarified": True}
            return stage, payload, "可以再补充一个具体事实吗？比如你做了什么、客户有什么回应，哪怕很小也可以。", False
        payload = {**payload, "small_win": text}
        return "strength", payload, SMALL_WIN_QUESTIONS["strength"], False
    if stage == "strength":
        return "gratitude", {**payload, "strength": text}, SMALL_WIN_QUESTIONS["gratitude"], False
    if stage == "gratitude":
        return "energy", {**payload, "gratitude": text}, SMALL_WIN_QUESTIONS["energy"], False
    # energy → 出卡（LLM 生成小赢卡；无模型或失败回退规则模板 _sw_render_card）
    payload = {**payload, "energy_sentence": _sw_energy(text, payload)}
    card = await _sw_llm_card(
        chat_model, payload,
        (prompts or {}).get("sw_card"), (prompts or {}).get("sw_system"),
    )
    if not card:
        card = _sw_render_card(payload)
    return "completed", payload, card, True


def _sw_render_card(payload: dict[str, Any]) -> str:
    small_win = payload.get("small_win") or "一个真实的小进展"
    strength = payload.get("strength") or "认真面对这件事"
    gratitude = payload.get("gratitude") or "自己没有放弃"
    energy = _sw_limit_energy(_sw_sanitize(payload.get("energy_sentence") or _sw_energy("", payload)))
    old_state, new_state = _sw_meaning_states(small_win)
    card = (
        "## 小赢卡\n\n"
        f"**今天的小赢**\n你今天完成了 / 推进了{small_win}。\n\n"
        f"**我欣赏你的是**\n你表现出了{strength}，比如你提到“{small_win}”。\n\n"
        f"**这件事的意义**\n它说明你正在从{old_state}走向{new_state}，哪怕只是一步。\n\n"
        f"**今天值得感谢**\n感谢{gratitude}。\n\n"
        f"**给自己的能量句**\n{energy}\n\n"
        "小赢不是小事，它是状态恢复和持续成长的起点。"
    )
    return _sw_sanitize(card)


async def _sw_llm_card(
    chat_model: Any, payload: dict[str, Any],
    tpl: str | None = None, system: str | None = None,
) -> str | None:
    """LLM 生成小赢卡（6 段固定结构）；无模型或失败时返回 None，由调用方回退 _sw_render_card。

    Args:
        tpl: 调用方经 ``PromptRegistry`` 解析的出卡模板；None 时回退内置。
        system: 调用方解析的 system 人设；None 时回退 SW_SYSTEM。
    """
    template = tpl or SW_CARD_TEMPLATE
    prompt = template.format(
        small_win=payload.get("small_win", ""),
        strength=payload.get("strength", ""),
        gratitude=payload.get("gratitude", ""),
        energy_sentence=payload.get("energy_sentence", ""),
    )
    return await _llm_generate(chat_model, system or SW_SYSTEM, prompt, call_site="coach_small_win")


def _sw_needs_clarify(text: str) -> bool:
    if len(text) < 8:
        return True
    return text in {"还行", "挺好", "有进展", "不错", "一般", "没什么"}


def _sw_energy(text: str, payload: dict[str, Any]) -> str:
    cleaned = _clean(text)
    if cleaned and cleaned not in {"AI代写", "AI 代写", "你帮我写", "帮我写"}:
        return _sw_limit_energy(cleaned)
    strength = payload.get("strength") or "稳住自己"
    return _sw_limit_energy(f"看见{strength}，继续稳稳向前")


def _sw_limit_energy(text: str) -> str:
    cleaned = _sw_sanitize(_clean(text))
    return cleaned[:25] if len(cleaned) > 25 else cleaned


def _sw_meaning_states(small_win: str) -> tuple[str, str]:
    if any(w in small_win for w in ("沉默", "没回复", "拒绝", "卡", "难")):
        return "停滞或消耗", "重新连接和推进"
    if any(w in small_win for w in ("主动", "联系", "沟通", "拜访", "回复")):
        return "等待和犹豫", "主动连接"
    return "忽略小进展", "看见自己的力量"


def _sw_sanitize(text: str) -> str:
    out = text or ""
    for phrase in _SMALL_WIN_FORBIDDEN:
        out = out.replace(phrase, "")
    return out


# ============================================================
# 卡点破框 — 3 问：卡点 → 事实/解释拆分 → 其他可能 → 出卡
# ============================================================

SB_FIRST = (
    "你先把这个卡点尽量说完整一点。客户怎么说的？发生了什么？"
    "你现在怎么判断？你纠结什么？你已经做过什么？"
)
SB_SECOND = (
    "我们先分一下：这里面哪些是客户明确说过、真实发生的事实？"
    "哪些是你的判断、猜测、担心或解释？"
)
SB_THIRD = (
    "除了你现在这个看法，还可能有什么其他解释？除了现在这个做法，我们还可以怎么做？\n"
    "- 这不一定是没兴趣，可能是客户还没看到优先级。\n"
    "- 这不一定是嫌贵，可能是客户怕买错、怕担责。\n"
    "- 这不一定是拒绝，可能是客户缺少内部推动理由。\n"
    "- 这不一定是不能推进，可能是下一步动作太大，需要变成小动作。\n"
    "- 这不一定是客户不回，可能是你还没有给他一个值得回应的理由。"
)


def _sb_start() -> tuple[str, dict[str, Any], str]:
    return "awaiting_blocker", {}, SB_FIRST


# ---- LLM 合成：高质量、贴合销售员实际输入 ----
# 第 2 步帮拆「事实/解释/担心」、第 4 步出 7 段破框卡，都用 LLM；
# 无模型或调用失败时回退到下面的规则版（_sb_split / _sb_final_reply）。

# SW_SYSTEM / SB_SYSTEM 已外移到 prompts/coach_quick.py（纳入 DB 版本管理），顶部 import。


async def _llm_generate(
    chat_model: Any, system: str, user: str, *,
    call_site: str,
) -> str | None:
    if chat_model is None:
        return None
    p = get_call_params(call_site)
    try:
        raw = await chat_model.generate(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=p.temperature,
            max_tokens=p.max_tokens,
        )
        out = (raw or "").strip()
        return out or None
    except Exception as e:  # noqa: BLE001
        logger.warning("quick-session LLM generate failed, fallback to rules: %s", e)
        return None


async def _sb_llm_split(
    chat_model: Any, sales_input: str, user_split: str,
    tpl: str | None = None, system: str | None = None,
) -> str | None:
    template = tpl or SB_SPLIT_TEMPLATE
    prompt = template.format(sales_input=sales_input, user_split=user_split)
    return await _llm_generate(chat_model, system or SB_SYSTEM, prompt, call_site="coach_block_split")


async def _sb_llm_card(
    chat_model: Any, payload: dict[str, Any],
    tpl: str | None = None, system: str | None = None,
) -> str | None:
    template = tpl or SB_CARD_TEMPLATE
    prompt = template.format(
        sales_input=payload.get("sales_input", ""),
        split_text=payload.get("split_text", ""),
        possibilities_attempt=payload.get("possibilities_attempt", ""),
    )
    return await _llm_generate(chat_model, system or SB_SYSTEM, prompt, call_site="coach_reframe")


async def _sb_advance(
    chat_model: Any, stage: str, payload: dict[str, Any], text: str,
    prompts: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any], str, bool]:
    text = (text or "").strip()
    if stage == "awaiting_blocker":
        return "awaiting_split", {**payload, "sales_input": text}, SB_SECOND, False

    if stage == "awaiting_split":
        sales_input = payload.get("sales_input", "")
        facts, interp, worries = _sb_split(text)  # 结构化（也供规则版出卡回退）
        split_text = await _sb_llm_split(
            chat_model, sales_input, text,
            (prompts or {}).get("sb_split"), (prompts or {}).get("sb_system"),
        )
        if not split_text:
            split_text = f"**事实**：{facts}\n**解释**：{interp}\n**担心**：{worries}"
        payload = {
            **payload,
            "facts": facts, "interpretations": interp, "worries": worries,
            "split_text": split_text, "split_raw": text,
        }
        # 先展示拆分（帮他分清事实和解释），再进入第三步
        reply = f"我帮你拆一下：\n\n{split_text}\n\n{SB_THIRD}"
        return "awaiting_possibilities", payload, reply, False

    # awaiting_possibilities → 出卡（LLM 生成 7 段；失败回退规则版）
    payload = {
        **payload,
        "possibilities_attempt": text,
        "new_frame": _sb_new_frame(text, payload),
        "minimum_action": _sb_minimum_action(payload),
    }
    card = await _sb_llm_card(
        chat_model, payload,
        (prompts or {}).get("sb_card"), (prompts or {}).get("sb_system"),
    )
    if not card:
        card = _sb_final_reply(payload)
    return "completed", payload, card, True


def _sb_split(text: str) -> tuple[str, str, str]:
    normalized = text.strip()
    if not normalized:
        return "还没有明确事实", "还没有明确解释", "还没有明确担心"
    facts = _sb_extract(normalized, ("事实", "真实发生", "客户说"))
    interp = _sb_extract(normalized, ("解释", "判断", "猜测", "觉得"))
    worries = _sb_extract(normalized, ("担心", "害怕", "怕"))
    if not facts:
        facts = normalized
    if not interp:
        interp = "把客户当前反应解释成推进阻力"
    if not worries:
        worries = "担心继续推进会带来负面反应"
    return (_clip(facts, 90), _clip(interp, 90), _clip(worries, 90))


def _sb_extract(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        idx = text.find(marker)
        if idx < 0:
            continue
        fragment = text[idx:]
        for sep in ("。", "\n", "；", ";"):
            end = fragment.find(sep)
            if end > 0:
                fragment = fragment[:end]
                break
        return fragment.strip(" ：:，,。")
    return ""


def _sb_new_frame(text: str, payload: dict[str, Any]) -> str:
    if text.strip():
        return _clip(text.strip(), 110)
    interp = payload.get("interpretations", "")
    sales_input = payload.get("sales_input", "")
    if "贵" in interp or "贵" in sales_input:
        return "客户不一定是嫌贵，可能是怕买错、怕担责，需要先降低决策风险。"
    if "不回" in sales_input:
        return "客户不回不一定是没兴趣，可能是你还没有给他一个值得回应的小问题。"
    return "这不一定是拒绝，可能是下一步动作太大，需要先换成一个低压力小动作。"


def _sb_minimum_action(payload: dict[str, Any]) -> str:
    sales_input = payload.get("sales_input", "")
    interp = payload.get("interpretations", "")
    if "不回" in sales_input:
        return "今天发一条只需要客户回复一句话的小问题，重新打开对话。"
    if "贵" in sales_input or "贵" in interp:
        return "今天先问清客户担心的是预算、效果还是内部担责，不急着降价。"
    if "约" in sales_input or "不敢" in sales_input:
        return "今天只约一个 15 分钟小沟通，不直接推进大决策。"
    return "今天发一个低压力确认问题，拿到客户下一步真实反应。"


def _sb_final_reply(payload: dict[str, Any]) -> str:
    return (
        f"1. 事实是什么：{payload.get('facts', '')}\n"
        f"2. 旧框是什么：{payload.get('interpretations', '')}\n"
        f"3. 新框是什么：{payload.get('new_frame', '')}\n"
        "4. 还可以怎么做：\n"
        "- 先问一个低压力确认问题。\n"
        "- 给客户一个具体回应理由。\n"
        "- 把大推进拆成一个小动作。\n"
        f"5. 今天的最小行动：{payload.get('minimum_action', '')}\n"
        f"6. 推荐话术：{_sb_script(payload)}\n"
        "7. 承诺和回传：你准备选哪个动作？几点前做？"
        "做完把客户反应发回来，我帮你复盘下一步。"
    )


def _sb_script(payload: dict[str, Any]) -> str:
    sales_input = payload.get("sales_input", "")
    interp = payload.get("interpretations", "")
    if "贵" in sales_input or "贵" in interp:
        return "我理解你会评估投入。方便说下你现在更担心预算、效果，还是内部不好推动？我按这个给你补一版更清楚的判断。"
    if "不回" in sales_input:
        return "我不催你定，只想确认一下：这件事现在是优先级往后放了，还是还缺一个内部推动理由？你回我一句就行。"
    return "我先不推进大决定，只想确认一个小点：如果下一步只花 15 分钟把关键问题过一遍，你看今天或明天哪个时间更合适？"


# ============================================================
# 通用工具
# ============================================================

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


_CANCEL_KEYWORDS = ("退出", "取消", "算了", "结束", "退出教练", "cancel", "exit", "quit", "/exit", "/cancel")


def _is_cancel(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in _CANCEL_KEYWORDS


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


_STARTERS = {
    "small_win_appreciation": _sw_start,
    "sales_block_breakthrough": _sb_start,
}
_ADVANCERS = {
    "small_win_appreciation": _sw_advance,
    "sales_block_breakthrough": _sb_advance,
}


# ============================================================
# Guided flow adapters (public API for the graph)
# ============================================================

from sales_agent.graph.guided_flow.types import FlowAdvance, FlowServices, FlowStart
from sales_agent.services.prompt_resolver_helper import resolve_quick_session_prompts


def start_small_win() -> FlowStart:
    stage, payload, reply = _sw_start()
    return FlowStart(stage=stage, payload=payload, reply=reply)


async def advance_small_win(
    stage: str,
    payload: dict,
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    prompts: dict[str, str] = {}
    if services.db is not None:
        prompts = await resolve_quick_session_prompts(
            services.db, services.tenant_id, services.agent_id
        )
    next_stage, next_payload, reply, completed = await _sw_advance(
        services.chat_model, stage, payload, text, prompts
    )
    return FlowAdvance(next_stage, next_payload, reply, completed)


def start_sales_block() -> FlowStart:
    stage, payload, reply = _sb_start()
    return FlowStart(stage=stage, payload=payload, reply=reply)


async def advance_sales_block(
    stage: str,
    payload: dict,
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    prompts: dict[str, str] = {}
    if services.db is not None:
        prompts = await resolve_quick_session_prompts(
            services.db, services.tenant_id, services.agent_id
        )
    next_stage, next_payload, reply, completed = await _sb_advance(
        services.chat_model, stage, payload, text, prompts
    )
    return FlowAdvance(next_stage, next_payload, reply, completed)


__all__ = [
    "SMALL_WIN_OPENING",
    "SMALL_WIN_QUESTIONS",
    "SB_FIRST",
    "SB_SECOND",
    "SB_THIRD",
    "_sw_start",
    "_sw_advance",
    "_sw_render_card",
    "_sw_llm_card",
    "_sw_needs_clarify",
    "_sw_energy",
    "_sw_limit_energy",
    "_sw_meaning_states",
    "_sw_sanitize",
    "_sb_start",
    "_sb_advance",
    "_sb_llm_split",
    "_sb_llm_card",
    "_sb_split",
    "_sb_extract",
    "_sb_new_frame",
    "_sb_minimum_action",
    "_sb_final_reply",
    "_sb_script",
    "_is_cancel",
    "_clip",
    "_clean",
    "_STARTERS",
    "_ADVANCERS",
    "start_small_win",
    "advance_small_win",
    "start_sales_block",
    "advance_sales_block",
]
