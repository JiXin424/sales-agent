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

from sales_agent.models.conversation_topic import ConversationTopic
from sales_agent.prompts.clarification_resolver_prompt import (
    CLARIFICATION_RESOLVER_PROMPT,
)
from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
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
            messages = [
                {"role": "system", "content": CLARIFICATION_RESOLVER_PROMPT},
                {"role": "user", "content": trimmed},
            ]
            response = await chat_model.generate(
                messages=messages,
                temperature=0.0,
                max_tokens=500,
            )
            return parse_model_json(response, ClarificationDecision)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "resolve_clarification parse failure: %s", exc,
            )

    # Default fallback
    return ClarificationDecision(resolution="new", confidence=0.5)
