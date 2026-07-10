"""Conversation logging node.

Persists the completed conversation turn and records latency stats.
Also updates the Topic summary when a ``topic_id`` is present.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langgraph.runtime import Runtime
from sales_agent.graph.chat.state import ChatGraphState
from sales_agent.services import conversation_logger
from sales_agent.services.topic_manager import TOPIC_IDLE_TIMEOUT

logger = logging.getLogger(__name__)


async def log_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Log the completed conversation turn to the database.

    When ``topic_id`` is present in state, also updates the Topic's
    summary, key entities, current goal, and timestamps using the
    Context Resolver output from the Online Graph.

    Args:
        state: Current graph state with all results populated.
        runtime: LangGraph runtime with context containing ``db``.

    Returns:
        Empty dict — this is a terminal node.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, skipping logging")
        return {}

    topic_id = state.get("topic_id")

    try:
        await conversation_logger.log_conversation(
            db,
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            channel=state.get("channel", "local"),
            agent_id=state.get("agent_id"),
            conversation_id=state["conversation_id"],
            message=state["message"],
            task_type=state.get("task_type", "general_sales_coaching"),
            task_confidence=state.get("route_confidence", 0.5),
            answer_dict=state.get("answer_dict", {}),
            risk_dict=state.get("risk_result", {}),
            sources=state.get("sources", []),
            model_config={},
            status="completed",
            stage_latency_ms={},
            path=state.get("path", "standard"),
            path_reason=state.get("path_reason", ""),
            topic_id=topic_id,
        )
    except Exception:
        logger.exception("Failed to persist completed online turn")
        raise

    # ── Topic summary update after successful turn ──────────────────
    if topic_id and db is not None:
        try:
            now = datetime.now(timezone.utc)

            # Load the Topic from DB
            from sqlalchemy import select
            from sales_agent.models.conversation_topic import ConversationTopic

            stmt = select(ConversationTopic).where(
                ConversationTopic.id == topic_id,
                ConversationTopic.tenant_id == state["tenant_id"],
            )
            result = await db.execute(stmt)
            topic = result.scalar_one_or_none()

            if topic is not None:
                # Use the answer_dict summary if available; cap at 500 chars.
                answer_dict = state.get("answer_dict", {}) or {}
                summary = answer_dict.get("summary", "")
                if summary:
                    topic.summary = summary[:500]
                else:
                    topic.summary = state.get("message", "")[:500]

                # Update key entities from retained_entities if available
                retained = state.get("retained_entities", [])
                topic.key_entities_json = json.dumps(
                    retained or [], ensure_ascii=False,
                )

                # Update current_goal from answer_dict or message
                topic.current_goal = answer_dict.get("summary", state.get("message", ""))[:500]

                # Refresh timestamps
                topic.last_active_at = now
                topic.expires_at = now + TOPIC_IDLE_TIMEOUT

                await db.flush()
                logger.debug("Updated topic %s summary and timestamps", topic_id)
        except Exception as e:
            logger.warning("Failed to update topic summary: %s", e)

    return {}
