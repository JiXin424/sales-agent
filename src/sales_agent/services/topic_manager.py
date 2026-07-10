"""Topic Manager — topic lifecycle, expiry, restore, and clarification.

Provides the persistence layer for conversation topic management:

- Topic creation, expiry (30 min idle), restore (24 h window)
- Clarification pending / resolve / cancel with timeout and max attempts
- Applying ContextDecision (continue / revise / switch / new) to topic state
- All queries filter by tenant, agent, user and channel

Important
---------
All topic mutation uses the **caller's transaction** — do **not** call
commit() inside this service.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.call_params import get_call_params
from sales_agent.models.conversation import ConversationMessage
from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.prompts.clarification_resolver_prompt import (
    CLARIFICATION_RESOLVER_PROMPT,
)
from sales_agent.services.prompt_resolver_helper import resolve_router_prompt
from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
    TopicScope,
    parse_model_json,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifecycle constants
# ---------------------------------------------------------------------------

TOPIC_IDLE_TIMEOUT = timedelta(minutes=30)
TOPIC_RESTORE_WINDOW = timedelta(hours=24)
CLARIFICATION_TIMEOUT = timedelta(minutes=30)
MAX_CLARIFICATION_ATTEMPTS = 2

# ---------------------------------------------------------------------------
# Exact command mappings for the clarification resolver
# ---------------------------------------------------------------------------

_CONTINUE_COMMANDS = ("继续", "接着刚才")
_NEW_COMMAND_PREFIXES = ("新问题", "换个话题")
_CANCEL_COMMANDS = ("取消", "算了")

# Regex to strip common leading punctuation after a command prefix
_LEADING_PUNCTUATION = re.compile(r"^[\s，,：:、。.]+")


def _strip_command_suffix(message: str, prefix: str) -> str | None:
    """Return the text after *prefix*, stripped of leading punctuation.

    Returns ``None`` when the remainder is empty / whitespace-only.
    """
    rest = message[len(prefix):]
    rest = _LEADING_PUNCTUATION.sub("", rest).strip()
    return rest or None


# ===================================================================
# TopicManager
# ===================================================================


class TopicManager:
    """Manage topic lifecycle, expiry, restore, and clarification state."""

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_active_topic(
        self,
        session: AsyncSession,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        channel: str,
    ) -> ConversationTopic | None:
        """Return the single active topic for *(tenant, agent, user, channel)*.

        The unique partial index ``uq_conversation_topic_active_scope``
        guarantees at most one active row per scope.
        """
        return await session.scalar(
            select(ConversationTopic).where(
                ConversationTopic.tenant_id == tenant_id,
                ConversationTopic.agent_id == agent_id,
                ConversationTopic.user_id == user_id,
                ConversationTopic.channel == channel,
                ConversationTopic.status == "active",
            )
        )

    async def find_restorable_topics(
        self,
        session: AsyncSession,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        channel: str,
        now: datetime | None = None,
    ) -> list[ConversationTopic]:
        """Return closed topics within the 24-hour restore window.

        Results are ordered from most-recently-closed to oldest.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - TOPIC_RESTORE_WINDOW
        result = await session.scalars(
            select(ConversationTopic)
            .where(
                ConversationTopic.tenant_id == tenant_id,
                ConversationTopic.agent_id == agent_id,
                ConversationTopic.user_id == user_id,
                ConversationTopic.channel == channel,
                ConversationTopic.status == "closed",
                ConversationTopic.closed_at >= cutoff,
            )
            .order_by(ConversationTopic.closed_at.desc())
        )
        return list(result.all())

    async def load_recent_topic_messages(
        self,
        session: AsyncSession,
        *,
        scope: TopicScope,
        conversation_id: str,
        topic_id: str,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        """Return the latest ``limit`` user/assistant messages for a topic.

        Filters by *(tenant, agent, user, channel, conversation_id, topic_id)*
        and role, orders newest-first to apply the limit, then reverses to
        chronological (oldest-first) order. Messages from any other topic —
        including a closed non-selected one — are never loaded.
        """
        result = await session.execute(
            select(ConversationMessage.role, ConversationMessage.content)
            .where(
                ConversationMessage.tenant_id == scope.tenant_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.topic_id == topic_id,
                ConversationMessage.role.in_(("user", "assistant")),
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        rows = list(result.all())
        rows.reverse()  # chronological order (oldest first)
        return [{"role": role, "content": content} for role, content in rows]

    # ------------------------------------------------------------------
    # Lifecycle mutations
    # ------------------------------------------------------------------

    async def close_if_expired(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
        now: datetime | None = None,
    ) -> bool:
        """Set *topic*'s status to ``closed`` if idle ``>= TOPIC_IDLE_TIMEOUT``.

        Returns ``True`` when the topic was closed.
        """
        if topic.status != "active":
            return False
        now = now or datetime.now(timezone.utc)
        if now - topic.last_active_at >= TOPIC_IDLE_TIMEOUT:
            topic.status = "closed"
            topic.closed_at = now
            return True
        return False

    async def restore_topic(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
        now: datetime | None = None,
    ) -> ConversationTopic:
        """Re-activate a closed topic, clearing its ``closed_at``.

        The caller is responsible for checking the 24-hour restore
        window via :meth:`find_restorable_topics` **before** calling
        this method.
        """
        now = now or datetime.now(timezone.utc)
        topic.status = "active"
        topic.closed_at = None
        topic.last_active_at = now
        topic.expires_at = now + TOPIC_IDLE_TIMEOUT
        return topic

    async def create_topic(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        channel: str,
        conversation_id: str,
        summary: str = "",
        key_entities: list[str] | None = None,
        current_goal: str = "",
        parent_topic_id: str | None = None,
        now: datetime | None = None,
    ) -> ConversationTopic:
        """Create and return a new active topic.

        The topic's ``expires_at`` is set to ``now + TOPIC_IDLE_TIMEOUT``.
        """
        now = now or datetime.now(timezone.utc)
        topic = ConversationTopic(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            channel=channel,
            conversation_id=conversation_id,
            status="active",
            summary=summary,
            key_entities_json=json.dumps(key_entities or [], ensure_ascii=False),
            current_goal=current_goal,
            parent_topic_id=parent_topic_id,
            last_active_at=now,
            expires_at=now + TOPIC_IDLE_TIMEOUT,
        )
        session.add(topic)
        return topic

    async def create_restore_anchor(
        self,
        session: AsyncSession,
        *,
        scope: TopicScope,
        conversation_id: str,
        event_id: str,
        original_message: str,
        candidates: list[ConversationTopic],
        now: datetime | None = None,
    ) -> ConversationTopic:
        """Create an *active* anchor topic holding a pending restore request.

        When 2+ candidates are restorable and the user cannot pick one, we
        create a fresh active topic whose ``pending_clarification_json``
        carries a ``kind: "topic_restore"`` payload (the candidate IDs and
        summaries). The anchor has **no** retained entities or prior goal.

        Because the partial unique active-Topic index allows only ONE active
        topic per scope, the caller must **close and flush** this anchor
        before restoring a candidate — otherwise the index would be
        violated. The anchor is the single active topic for its scope while
        it exists.
        """
        now = now or datetime.now(timezone.utc)
        anchor = ConversationTopic(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            channel=scope.channel,
            conversation_id=conversation_id,
            status="active",
            summary="",
            current_goal="",
            key_entities_json="[]",
            pending_clarification_json=json.dumps(
                {
                    "kind": "topic_restore",
                    "event_id": event_id,
                    "original_message": original_message,
                    "candidate_topic_ids": [c.id for c in candidates],
                    "candidate_summaries": [c.summary for c in candidates],
                    "created_at": now.isoformat(),
                },
                ensure_ascii=False,
            ),
            last_active_at=now,
            expires_at=now + TOPIC_IDLE_TIMEOUT,
        )
        session.add(anchor)
        return anchor

    # ------------------------------------------------------------------
    # Apply ContextDecision
    # ------------------------------------------------------------------

    async def apply_context_decision(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
        decision: ContextDecision,
        now: datetime | None = None,
    ) -> ConversationTopic:
        """Apply a :class:`ContextDecision` to *topic*.

        Behaviour per ``turn_relation``:

        * **continue** — refresh timestamps, update summary / entities.
        * **revise**   — same topic; record retracted goals.
        * **switch**   — close current topic; create a child topic carrying
          only ``retained_entities``.
        * **new**      — close current topic (caller creates a fresh one).
        * **ambiguous** — refresh ``last_active_at`` only.
        """
        now = now or datetime.now(timezone.utc)

        # -- continue ---------------------------------------------------
        if decision.turn_relation == "continue":
            topic.last_active_at = now
            topic.expires_at = now + TOPIC_IDLE_TIMEOUT
            if decision.standalone_query:
                topic.current_goal = decision.standalone_query
                topic.summary = decision.standalone_query
            if decision.retained_entities:
                topic.key_entities_json = json.dumps(
                    decision.retained_entities, ensure_ascii=False,
                )
            return topic

        # -- revise -----------------------------------------------------
        if decision.turn_relation == "revise":
            topic.last_active_at = now
            topic.expires_at = now + TOPIC_IDLE_TIMEOUT
            if decision.standalone_query:
                topic.current_goal = decision.standalone_query
            if decision.retained_entities:
                topic.key_entities_json = json.dumps(
                    decision.retained_entities, ensure_ascii=False,
                )
            if decision.retracted_goals:
                topic.retracted_goals_json = json.dumps(
                    decision.retracted_goals, ensure_ascii=False,
                )
            return topic

        # -- switch -----------------------------------------------------
        if decision.turn_relation == "switch":
            # Close the current topic
            topic.status = "closed"
            topic.closed_at = now

            # Create a child topic with only selected entities
            child = await self.create_topic(
                session,
                tenant_id=topic.tenant_id,
                agent_id=topic.agent_id,
                user_id=topic.user_id,
                channel=topic.channel,
                conversation_id=topic.conversation_id,
                summary=decision.standalone_query,
                key_entities=decision.retained_entities,
                current_goal=decision.standalone_query,
                parent_topic_id=topic.id,
                now=now,
            )
            return child

        # -- new --------------------------------------------------------
        if decision.turn_relation == "new":
            topic.status = "closed"
            topic.closed_at = now
            return topic

        # -- ambiguous (fallback) ---------------------------------------
        topic.last_active_at = now
        return topic

    # ------------------------------------------------------------------
    # Clarification state
    # ------------------------------------------------------------------

    async def set_pending_clarification(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
        event_id: str,
        original_message: str,
        candidate_query: str,
    ) -> ConversationTopic:
        """Store a pending clarification request on *topic*.

        Idempotent on ``event_id`` — calling with the same event ID
        will **not** overwrite the existing pending state.

        The stored JSON contains ``event_id``, ``original_message``,
        ``candidate_query``, and ``created_at`` (UTC ISO).
        """
        if topic.pending_clarification_json:
            existing = json.loads(topic.pending_clarification_json)
            if existing.get("event_id") == event_id:
                return topic

        pending = {
            "event_id": event_id,
            "original_message": original_message,
            "candidate_query": candidate_query,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        topic.pending_clarification_json = json.dumps(
            pending, ensure_ascii=False,
        )
        return topic

    async def cancel_pending(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
    ) -> ConversationTopic:
        """Clear any pending clarification and reset the attempt counter."""
        topic.pending_clarification_json = None
        topic.clarification_attempts = 0
        return topic

    async def resolve_pending(
        self,
        session: AsyncSession,
        topic: ConversationTopic,
        decision: ClarificationDecision,
        now: datetime | None = None,
    ) -> ConversationTopic:
        """Apply a :class:`ClarificationDecision` and clear pending state.

        Behaviour per ``resolution``:

        * **continue** — update ``current_goal`` with ``supplemental_message``
          or ``replacement_text``; keep topic active.
        * **new**      — close the topic (caller creates a fresh one).
        * **replace**  — replace ``current_goal`` with ``replacement_text``.
        * **cancel**   — close the topic.
        """
        now = now or datetime.now(timezone.utc)

        if decision.resolution == "continue":
            topic.pending_clarification_json = None
            topic.clarification_attempts = 0
            topic.last_active_at = now
            if decision.supplemental_message:
                topic.current_goal = decision.supplemental_message
            elif decision.replacement_text:
                topic.current_goal = decision.replacement_text
            return topic

        if decision.resolution == "new":
            topic.pending_clarification_json = None
            topic.clarification_attempts = 0
            topic.status = "closed"
            topic.closed_at = now
            return topic

        if decision.resolution == "replace":
            topic.pending_clarification_json = None
            topic.clarification_attempts = 0
            topic.last_active_at = now
            if decision.replacement_text:
                topic.current_goal = decision.replacement_text
            return topic

        if decision.resolution == "cancel":
            topic.pending_clarification_json = None
            topic.clarification_attempts = 0
            topic.status = "closed"
            topic.closed_at = now
            return topic

        # Unknown resolution — treat as cancel (safe default)
        logger.warning("Unknown clarification resolution: %s", decision.resolution)
        topic.pending_clarification_json = None
        topic.clarification_attempts = 0
        topic.status = "closed"
        topic.closed_at = now
        return topic


# ===================================================================
# Clarification Resolver
# ===================================================================


async def resolve_clarification(
    message: str,
    chat_model: Any,
    attempt_count: int = 0,
    *,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> ClarificationDecision:
    """Resolve a clarification need based on the user's *message*.

    *Exact command prefixes* are matched without an LLM call:

    ======================  =============
    Message prefix          Resolution
    ======================  =============
    ``继续`` / ``接着刚才``  continue
    ``新问题`` / ``换个话题`` new
    ``取消`` / ``算了``      cancel
    ======================  =============

    Other messages are forwarded to *chat_model* with the
    :data:`CLARIFICATION_RESOLVER_PROMPT`. When *attempt_count* reaches
    :data:`MAX_CLARIFICATION_ATTEMPTS` the function defaults to ``new``
    without calling the model.

    Parameters
    ----------
    db :
        数据库会话；非空且 *tenant_id* 有值时走 PromptRegistry 三级回退
        （运营后台编辑生效），否则回退到模块常量。
    tenant_id, agent_id :
        租户 / Agent 标识，用于 PromptRegistry 解析。
    """
    trimmed = message.strip()

    # Safety valve: too many ambiguous attempts → force new
    if attempt_count >= MAX_CLARIFICATION_ATTEMPTS:
        return ClarificationDecision(resolution="new", confidence=1.0)

    # -- Exact command matching without LLM --------------------------------
    # continue commands
    for cmd in _CONTINUE_COMMANDS:
        if trimmed.startswith(cmd):
            supplemental = _strip_command_suffix(trimmed, cmd)
            return ClarificationDecision(
                resolution="continue",
                supplemental_message=supplemental,
                confidence=1.0,
            )

    # new commands
    for cmd in _NEW_COMMAND_PREFIXES:
        if trimmed.startswith(cmd):
            return ClarificationDecision(resolution="new", confidence=1.0)

    # cancel commands
    for cmd in _CANCEL_COMMANDS:
        if trimmed.startswith(cmd):
            return ClarificationDecision(resolution="cancel", confidence=1.0)

    # -- LLM-based resolution ----------------------------------------------
    if chat_model is not None:
        try:
            # 命中 LLM 段才解析 prompt，避免无谓 DB 往返（短路命令不调 LLM）
            system_prompt = await resolve_router_prompt(
                db,
                "clarification_resolver",
                tenant_id,
                agent_id,
                default=CLARIFICATION_RESOLVER_PROMPT,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": trimmed},
            ]
            p = get_call_params("topic_manager")
            response = await chat_model.generate(
                messages=messages,
                temperature=p.temperature,
                max_tokens=p.max_tokens,
            )
            return parse_model_json(response, ClarificationDecision)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "resolve_clarification parse failure: %s", exc,
            )

    # Default fallback
    return ClarificationDecision(resolution="new", confidence=0.5)
