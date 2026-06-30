"""DingTalk streaming via LangGraph ``astream()``.

Replaces ``streaming_handler.py``'s duplicated pipeline logic.
The graph handles all pipeline stages; this module only manages
the DingTalk interactive card lifecycle.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer

logger = logging.getLogger(__name__)


async def handle_dingtalk_stream_via_graph(
    *,
    tenant_id: str,
    user_id: str,
    dingtalk_user_id: str,
    message: str,
    conversation_id: str,
    agent_id: str | None,
    reply_fn: Callable[[str], Awaitable[None]],
    card_sender,  # DingTalkCardSender
    db,  # AsyncSession
    chat_model,  # ChatModel instance
) -> dict:
    """Process a DingTalk stream message through the LangGraph ChatPipeline.

    The graph handles all pipeline stages (routing, retrieval, generation).
    This function only manages the DingTalk card lifecycle:

    1. Create an initial "thinking..." card
    2. Stream token updates from ``graph.astream(stream_mode="messages")``
    3. On node completion (``stream_mode="updates"``), update card with sections
    4. Finalize the card with the complete answer

    Args:
        tenant_id: Tenant identifier.
        user_id: Internal user identifier.
        dingtalk_user_id: DingTalk user ID (staff_id) for card delivery.
        message: User's message text.
        conversation_id: Conversation thread identifier.
        agent_id: Resolved agent identifier.
        reply_fn: Callback to send DingTalk messages (fallback).
        card_sender: DingTalkCardSender instance for interactive cards.
        db: Async SQLAlchemy session.
        chat_model: Configured chat model instance.

    Returns:
        The final graph state dict.
    """
    checkpointer = await get_checkpointer()

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            "thread_id": conversation_id or str(uuid.uuid4()),
        }
    }

    input_state = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "message": message,
        "conversation_id": conversation_id,
        "channel": "dingtalk",
        "agent_id": agent_id,
    }

    # 1. Create initial card
    card_id = await card_sender.send_markdown_card(
        dingtalk_user_id=dingtalk_user_id,
        title="思考中...",
        markdown_text="正在分析你的问题...",
    )

    # 2. Stream: messages for token-by-token, updates for completion
    final_answer = None
    accumulated_text = ""

    async for chunk in graph.astream(
        input_state,
        config,
        context={"db": db, "chat_model": chat_model},
        stream_mode=["messages", "updates"],
    ):
        if chunk["type"] == "messages":
            # Token-by-token: update the streaming card
            msg_data = chunk["data"]
            if hasattr(msg_data[0], "content"):
                accumulated_text += msg_data[0].content or ""
                await card_sender.streaming_update(card_id, accumulated_text)

        elif chunk["type"] == "updates":
            data = chunk["data"]
            # Check if any node emitted an answer_dict
            for node_name, node_output in data.items():
                if isinstance(node_output, dict) and "answer_dict" in node_output:
                    final_answer = node_output["answer_dict"]
                    break

    # 3. Finalize card with parsed answer
    if final_answer is None:
        final_answer = {"summary": accumulated_text, "sections": []}

    await card_sender.streaming_finalize(card_id, accumulated_text)

    return {"answer_dict": final_answer}
