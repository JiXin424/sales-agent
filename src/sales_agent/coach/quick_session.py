"""快捷入口多轮教练对话 — 小赢欣赏 / 卡点破框。

逻辑移植自 Yanshi_Omni_Agent 的 ``api/core/coach/small_win.py`` 与
``sales_block_breakthrough.py``（已验证的文案与状态机），改为**落库**存会话状态：
每次点击按钮 ``start_session`` 建一条 ``quick_sessions`` 记录并发首轮提问；
用户在钉钉单聊里的后续回复由 ``streaming_handler`` 顶部调用
``advance_active_session`` 推进，直到 ``completed`` 出卡。

对外只暴露三个入口：
  - ``VALID_TYPES`` / ``label_of``
  - ``start_session(db, tenant_id, external_user_id, session_type, ...) -> str``（首轮提问）
  - ``advance_active_session(db, tenant_id, external_user_id, user_text) -> (reply, completed) | None``
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.quick_session import QuickSession

logger = logging.getLogger(__name__)

VALID_TYPES = ("small_win_appreciation", "sales_block_breakthrough")

_LABELS = {
    "small_win_appreciation": "小赢欣赏",
    "sales_block_breakthrough": "卡点破框",
}


def label_of(session_type: str) -> str:
    return _LABELS.get(session_type, session_type)


# 出卡后“软关闭”窗口：会话 completed 后 N 秒内，该用户在单聊里的回复仍由
# 本会话拦截（回一句固定确认语、不走 LLM），避免出卡文案末尾“承诺和回传”
# 诱导的回复掉进普通销售对话管道。超窗后才放行普通对话。
FOLLOWUP_WINDOW_SECONDS = 600

_FOLLOWUP_REPLIES = {
    "small_win_appreciation": (
        "✅ 这条小赢欣赏已结束。\n"
        "小赢已记下，继续保持，下一个进展随时再来。"
    ),
    "sales_block_breakthrough": (
        "✅ 这条卡点破框已结束。\n"
        "你按选定的最小行动做完，把客户反应发回来；"
        "要拆下一个卡点，就再点「卡点破框」重新开始。"
    ),
}
_FOLLOWUP_DEFAULT = "✅ 这条快捷对话已结束，需要时再点入口重新开始。"


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
    card = await _sw_llm_card(chat_model, payload)
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


async def _sw_llm_card(chat_model: Any, payload: dict[str, Any]) -> str | None:
    """LLM 生成小赢卡（6 段固定结构）；无模型或失败时返回 None，由调用方回退 _sw_render_card。"""
    prompt = (
        "销售员刚做完一次「小赢欣赏」对话，下面是他逐轮说出的原始内容：\n"
        f"【今天的小赢】{payload.get('small_win', '')}\n"
        f"【他觉得自己做得不错的地方】{payload.get('strength', '')}\n"
        f"【他想感谢的】{payload.get('gratitude', '')}\n"
        f"【他的能量句（可能为“AI代写”或空）】{payload.get('energy_sentence', '')}\n\n"
        "请严格按下面格式输出小赢卡，不要加额外标题或开场白，每段都基于上面内容填实：\n"
        "## 小赢卡\n\n"
        "**今天的小赢**\n用他的小赢原文改写成一句完整的话。\n\n"
        "**我欣赏你的是**\n你表现出了【一个具体优势/品质】，比如【引用他小赢里的具体证据】。\n\n"
        "**这件事的意义**\n它说明你正在从【旧状态/困难】走向【新状态/可能性】，哪怕只是一步。\n\n"
        "**今天值得感谢**\n用他的感谢原话改写，落到具体对象。\n\n"
        "**给自己的能量句**\n一句短而真诚的鼓励，不超过 25 字；若他给了能量句且不是“AI代写”，可润色后用。\n\n"
        "最后以这句收尾：小赢不是小事，它是状态恢复和持续成长的起点。"
    )
    return await _llm_generate(chat_model, SW_SYSTEM, prompt, max_tokens=600, temperature=0.4)


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

SB_SYSTEM = (
    "你是一位有经验的销售军师，专门帮一线销售拆解卡点、给出今天就能做的最小行动。"
    "语气清晰、坚定、支持行动。要求：不要一开始就给建议；不要讲大道理；"
 "不要泛泛鼓励；每次只推动一个最小行动；话术要短、自然、具体。"
)

# 小赢欣赏出卡（第 4 步小赢卡）的 LLM 人设；无模型或调用失败时回退 _sw_render_card。
SW_SYSTEM = (
    "你是一位温暖、具体的销售教练，帮一线销售把今天的一个小进展写成一张「小赢卡」。"
    "语气平实真诚、不煽情、不堆感叹号；每段都要落到他说的具体事实，不要编造没说过的细节。"
)


async def _llm_generate(
    chat_model: Any, system: str, user: str, *,
    max_tokens: int = 900, temperature: float = 0.4,
) -> str | None:
    if chat_model is None:
        return None
    try:
        raw = await chat_model.generate(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        out = (raw or "").strip()
        return out or None
    except Exception as e:  # noqa: BLE001
        logger.warning("quick-session LLM generate failed, fallback to rules: %s", e)
        return None


async def _sb_llm_split(chat_model: Any, sales_input: str, user_split: str) -> str | None:
    prompt = (
        "销售员正在拆解一个销售卡点。\n"
        f"【他的卡点描述】{sales_input}\n"
        f"【他对「事实 vs 解释」的拆分】{user_split}\n\n"
        "请基于他实际说的内容，帮他拆成三类，简洁列出，不要编造、不要加建议：\n"
        "**事实**：（客户明确说过、真实发生的事）\n"
        "**解释**：（他的判断、猜测、推断）\n"
        "**担心**：（他害怕或顾虑的）"
    )
    return await _llm_generate(chat_model, SB_SYSTEM, prompt, max_tokens=400, temperature=0.2)


async def _sb_llm_card(chat_model: Any, payload: dict[str, Any]) -> str | None:
    prompt = (
        "销售员正在拆一个销售卡点，下面是这次对话收集到的内容：\n"
        f"【卡点描述】{payload.get('sales_input', '')}\n"
        f"【事实/解释/担心 拆分】{payload.get('split_text', '')}\n"
        f"【他想到的其他可能】{payload.get('possibilities_attempt', '')}\n\n"
        "请严格按下面 7 条格式输出，不要加额外标题或开场白，每条都要基于上面内容填实：\n"
        "1. 事实是什么：简洁列出真实发生的事。\n"
        "2. 旧框是什么：总结他现在卡住自己的那个解释。\n"
        "3. 新框是什么：给一个更有行动力的新解释。\n"
        "4. 还可以怎么做：给 2-3 个可选做法，每条一行，用「- 」开头。\n"
        "5. 今天的最小行动：只给一个最推荐、今天就能做的动作。\n"
        "6. 推荐话术：给一段可以直接复制发给客户的话，短、自然、具体。\n"
        "7. 承诺和回传：你准备选哪个动作？几点前做？做完把客户反应发回来，我帮你复盘下一步。"
    )
    return await _llm_generate(chat_model, SB_SYSTEM, prompt, max_tokens=900, temperature=0.4)


async def _sb_advance(
    chat_model: Any, stage: str, payload: dict[str, Any], text: str,
) -> tuple[str, dict[str, Any], str, bool]:
    text = (text or "").strip()
    if stage == "awaiting_blocker":
        return "awaiting_split", {**payload, "sales_input": text}, SB_SECOND, False

    if stage == "awaiting_split":
        sales_input = payload.get("sales_input", "")
        facts, interp, worries = _sb_split(text)  # 结构化（也供规则版出卡回退）
        split_text = await _sb_llm_split(chat_model, sales_input, text)
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
    card = await _sb_llm_card(chat_model, payload)
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
# DB 会话服务
# ============================================================


async def _get_active(
    db: AsyncSession, tenant_id: str, external_user_id: str, channel: str = "dingtalk",
) -> QuickSession | None:
    stmt = (
        select(QuickSession)
        .where(
            QuickSession.tenant_id == tenant_id,
            QuickSession.external_user_id == external_user_id,
            QuickSession.channel == channel,
            QuickSession.status == "active",
        )
        .order_by(QuickSession.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_recent_completed(
    db: AsyncSession,
    tenant_id: str,
    external_user_id: str,
    channel: str,
    within_seconds: int,
) -> QuickSession | None:
    """查该用户最近 within_seconds 内「自然走完出卡」的快捷会话。

    仅命中 stage=completed 的会话（_sw/_sb_advance 出卡时置 stage=completed）；
    主动退出（_is_cancel）或被作废的旧会话 stage 仍为中间值，故不拦截——那些
    场景用户应能正常提问。updated_at 在 completed 时由 onupdate 刷新为出卡时刻。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).isoformat()
    stmt = (
        select(QuickSession)
        .where(
            QuickSession.tenant_id == tenant_id,
            QuickSession.external_user_id == external_user_id,
            QuickSession.channel == channel,
            QuickSession.status == "completed",
            QuickSession.stage == "completed",
            QuickSession.updated_at >= cutoff,
        )
        .order_by(QuickSession.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def has_active_or_recent_session(
    db: AsyncSession, tenant_id: str, external_user_id: str, channel: str = "dingtalk",
) -> bool:
    """是否存在活跃会话、或出卡后软关闭窗口内的会话。供上层决定是否解析模型。"""
    if await _get_active(db, tenant_id, external_user_id, channel):
        return True
    return await _get_recent_completed(
        db, tenant_id, external_user_id, channel, FOLLOWUP_WINDOW_SECONDS,
    ) is not None


async def start_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    external_user_id: str,
    session_type: str,
    agent_id: str | None = None,
    channel: str = "dingtalk",
) -> str:
    """开始一次快捷入口会话：作废旧活跃会话，建新会话，返回首轮提问文案。"""
    if session_type not in VALID_TYPES:
        raise ValueError(f"invalid session_type: {session_type}")

    # 作废该用户当前所有活跃会话（同一时间只进行一个）
    existing = await _get_active(db, tenant_id, external_user_id, channel)
    if existing is not None:
        existing.status = "completed"

    stage, payload, first_reply = _STARTERS[session_type]()
    session = QuickSession(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel=channel,
        external_user_id=external_user_id,
        session_type=session_type,
        stage=stage,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="active",
    )
    db.add(session)
    await db.flush()
    logger.info(
        "quick-session started: type=%s user=%s stage=%s",
        session_type, external_user_id, stage,
    )
    return first_reply


async def advance_active_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    external_user_id: str,
    user_text: str,
    channel: str = "dingtalk",
    chat_model: Any = None,
) -> tuple[str, bool] | None:
    """若有活跃会话则推进一轮，返回 (reply, completed)。

    无活跃会话时：若该用户最近 ``FOLLOWUP_WINDOW_SECONDS`` 秒内有过自然出卡的快捷
    会话（出卡后软关闭窗口），仍拦截其回复并返回确认语 ``(reply, True)``，避免出卡
    文案诱导的承诺/回传回复掉进普通 LLM 对话；否则返回 None，交由上层走普通管道。
    """
    session = await _get_active(db, tenant_id, external_user_id, channel)
    if session is None:
        recent = await _get_recent_completed(
            db, tenant_id, external_user_id, channel, FOLLOWUP_WINDOW_SECONDS,
        )
        if recent is not None:
            logger.info(
                "quick-session followup ack: type=%s user=%s completed_at=%s",
                recent.session_type, external_user_id, recent.updated_at,
            )
            return _FOLLOWUP_REPLIES.get(recent.session_type, _FOLLOWUP_DEFAULT), True
        logger.info(
            "quick-session miss: tenant=%s user=%s channel=%s -> fallback to pipeline",
            tenant_id, external_user_id, channel,
        )
        return None

    # 允许用户随时退出多轮会话，避免被「锁」在流程里无法正常提问
    if _is_cancel(user_text):
        session.status = "completed"
        await db.flush()
        logger.info(
            "quick-session cancelled by user: type=%s user=%s",
            session.session_type, external_user_id,
        )
        return f"已退出「{label_of(session.session_type)}」，你可以正常提问了。", True

    advancer = _ADVANCERS.get(session.session_type)
    if advancer is None:
        return None

    try:
        payload = json.loads(session.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}

    new_stage, new_payload, reply, completed = await advancer(
        chat_model, session.stage, payload, user_text,
    )
    session.stage = new_stage
    session.payload_json = json.dumps(new_payload, ensure_ascii=False)
    if completed:
        session.status = "completed"
    await db.flush()
    logger.info(
        "quick-session advanced: type=%s user=%s stage=%s completed=%s",
        session.session_type, external_user_id, new_stage, completed,
    )
    return reply, completed
