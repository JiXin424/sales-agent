"""Topic Restore Resolver — decide which closed candidate to restore.

Given the user's *message* and a list of restorable candidate topics (closed
within the 24-hour window), decide whether to:

- **restore** a specific candidate,
- start a **new** topic, or
- mark the request **ambiguous** (the caller then creates a restore anchor).

The resolver is a *separate* vocabulary from the Context Resolver's
``turn_relation``: it only chooses among candidates. Exact numeric and
new-topic commands are handled deterministically (no LLM); everything else
is forwarded to the model with the candidate IDs + summaries, and the
returned ``selected_topic_id`` is validated against the supplied candidate
map — the model may never invent an arbitrary Topic ID.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.call_params import get_call_params
from sales_agent.llm.prompt_loader import get_prompt
from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.services.structured_router_output import (
    TopicRestoreDecision,
    parse_model_json,
)

logger = logging.getLogger(__name__)

# After this many unresolved (ambiguous) answers, the safe fallback is `new`.
MAX_RESTORE_ATTEMPTS = 2

# Explicit new-topic commands (deterministic, no LLM).
_RESTORE_NEW_COMMAND_PREFIXES = ("新问题", "新话题", "换话题", "换个话题")

# Continue marker — explicit "继续".
_RESTORE_CONTINUE_COMMAND = "继续"

# Numeric selection: 第1个 / 第一个 / 第2个 ... (Arabic or single CJK numeral).
_NUMERIC_PREFIX_RE = re.compile(r"^第\s*([一二三四五六七八九十]|\d+)\s*个")
# Bare leading integer: "1" / "2" optionally followed by separator + suffix.
_BARE_NUMERIC_RE = re.compile(r"^(\d+)")

# Punctuation to strip between a command and its suffix.
_LEADING_PUNCTUATION = re.compile(r"^[\s，,：:、。.]+")

# CJK numeral → int (single-char, enough for candidate lists ≤ a handful).
_CJK_NUMERAL = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _strip_suffix(message: str, prefix: str) -> str | None:
    """Return the text after *prefix*, stripped of leading punctuation."""
    rest = message[len(prefix):]
    rest = _LEADING_PUNCTUATION.sub("", rest).strip()
    return rest or None


def _parse_numeric(message: str) -> tuple[int, str | None] | None:
    """Parse a leading numeric selector.

    Returns ``(index_1_based, optional_suffix)`` or ``None`` when *message*
    does not start with a numeric selector.
    """
    trimmed = message.strip()

    m = _NUMERIC_PREFIX_RE.match(trimmed)
    if m:
        token = m.group(1)
        n = _CJK_NUMERAL.get(token)
        if n is None:
            n = int(token)
        rest = trimmed[m.end():]
        rest = _LEADING_PUNCTUATION.sub("", rest).strip()
        return n, (rest or None)

    m = _BARE_NUMERIC_RE.match(trimmed)
    if m:
        n = int(m.group(1))
        rest = trimmed[m.end():]
        rest = _LEADING_PUNCTUATION.sub("", rest).strip()
        return n, (rest or None)

    return None


async def resolve_topic_restore(
    *,
    message: str,
    candidates: list[ConversationTopic],
    attempt_count: int = 0,
    chat_model: Any,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> TopicRestoreDecision:
    """Resolve which candidate (if any) the user wants to restore.

    Decision order:

    1. **Safety valve** — ``attempt_count >= MAX_RESTORE_ATTEMPTS`` → ``new``.
    2. **Numeric** — ``第1个`` / ``第一个`` / ``1`` → ``restore`` candidate N
       (with optional suffix as ``supplemental_message``). Out-of-range →
       ``ambiguous``.
    3. **New command** — ``新问题`` / ``新话题`` / ``换话题`` → ``new``
       (suffix becomes the supplemental task).
    4. **Continue** — ``继续``: one candidate → ``restore``; multiple →
       ``ambiguous`` (caller creates a restore anchor). Suffix is preserved.
    5. **Model** — otherwise forward candidate IDs + summaries to the model
       and validate the returned ``selected_topic_id``.

    Parameters
    ----------
    candidates :
        Closed topics within the 24-hour window (already filtered by
        :meth:`TopicManager.find_restorable_topics`).
    attempt_count :
        Number of prior unresolved answers for this anchor.
    """
    # 1. Safety valve: too many unresolved attempts → force new.
    if attempt_count >= MAX_RESTORE_ATTEMPTS:
        return TopicRestoreDecision(
            resolution="new",
            supplemental_message=message.strip() or None,
            confidence=1.0,
            reason_code="max_attempts_exceeded",
        )

    candidate_ids = [c.id for c in candidates]

    # 2. Numeric selection.
    numeric = _parse_numeric(message)
    if numeric is not None:
        index, suffix = numeric
        if 1 <= index <= len(candidates):
            return TopicRestoreDecision(
                resolution="restore",
                selected_topic_id=candidates[index - 1].id,
                supplemental_message=suffix,
                confidence=1.0,
                reason_code="numeric_selection",
            )
        # Out of range — ambiguous so the caller re-asks.
        return TopicRestoreDecision(
            resolution="ambiguous",
            supplemental_message=suffix,
            confidence=0.6,
            reason_code="numeric_out_of_range",
        )

    trimmed = message.strip()

    # 3. New-topic command.
    for cmd in _RESTORE_NEW_COMMAND_PREFIXES:
        if trimmed.startswith(cmd):
            suffix = _strip_suffix(trimmed, cmd)
            return TopicRestoreDecision(
                resolution="new",
                supplemental_message=suffix,
                confidence=1.0,
                reason_code="explicit_new_command",
            )

    # 4. Continue command — deterministic restore vs. ambiguous anchor.
    if trimmed.startswith(_RESTORE_CONTINUE_COMMAND):
        suffix = _strip_suffix(trimmed, _RESTORE_CONTINUE_COMMAND)
        if len(candidates) == 1:
            return TopicRestoreDecision(
                resolution="restore",
                selected_topic_id=candidates[0].id,
                supplemental_message=suffix,
                confidence=1.0,
                reason_code="continue_unique",
            )
        return TopicRestoreDecision(
            resolution="ambiguous",
            supplemental_message=suffix,
            confidence=0.6,
            reason_code="continue_multiple_candidates",
        )

    # 5. Model-based resolution.
    if chat_model is not None and candidates:
        try:
            system_prompt = get_prompt("router", "topic_restore_resolver").template
            candidate_json = json.dumps(
                [
                    {"topic_id": c.id, "summary": c.summary}
                    for c in candidates[:3]
                ],
                ensure_ascii=False,
            )
            user_content = f"用户消息：{trimmed}\n候选列表：{candidate_json}\n"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            p = get_call_params("topic_restore")
            response = await chat_model.generate(
                messages=messages,
                temperature=p.temperature,
                max_tokens=p.max_tokens,
            )
            decision = parse_model_json(response, TopicRestoreDecision)
            # Validate the model's selection against the supplied candidate map.
            if (
                decision.resolution == "restore"
                and decision.selected_topic_id not in candidate_ids
            ):
                logger.warning(
                    "resolve_topic_restore: model returned unknown id %s; "
                    "treating as ambiguous",
                    decision.selected_topic_id,
                )
                return TopicRestoreDecision(
                    resolution="ambiguous",
                    supplemental_message=None,
                    confidence=0.4,
                    reason_code="invalid_selected_id",
                )
            return decision
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("resolve_topic_restore parse failure: %s", exc)

    # Default fallback — safe new topic rather than guessing.
    return TopicRestoreDecision(
        resolution="new",
        supplemental_message=trimmed or None,
        confidence=0.5,
        reason_code="restore_resolver_unavailable",
    )
