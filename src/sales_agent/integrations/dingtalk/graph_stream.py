"""DingTalk streaming via LangGraph ``astream()``.

Replaces ``streaming_handler.py``'s duplicated pipeline logic.
The graph handles all pipeline stages; this module only manages
the DingTalk interactive card lifecycle.

P1: Custom stream events from nodes (via ``runtime.stream_writer``)
     drive the card progress display: retrieval progress → generation
     progress → done. The card shows clear phase transitions instead
     of a single "thinking..." spinner.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from sales_agent.graph.chat_graph import build_chat_graph_compiled
from sales_agent.graph.checkpoints import get_checkpointer, get_store

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
    chat_model,  # ChatModel instance (P1: was None — now required for graph)
) -> dict:
    """Process a DingTalk stream message through the LangGraph ChatPipeline.

    The graph handles all pipeline stages (routing, retrieval, generation).
    This function only manages the DingTalk card lifecycle:

    1. Create an initial "analyzing..." card
    2. Stream three modes concurrently:
       - ``messages``: token-by-token LLM output → card text
       - ``updates``: node completion → extract final answer
       - ``custom``: progress events from nodes → card subtitle updates
    3. Finalize the card with the complete answer

    P1: ``stream_mode="custom"`` events from nodes
    (``runtime.stream_writer``) update the card's subtitle in real-time:
    - ```retrieval_started``` → "🔍 检索知识库中..."
    - ```retrieval_complete``` → "✅ 找到 N 条相关资料"
    - ```generation_started``` → "✍️ 开始生成回答..."
    - ```generation_executing``` → "✍️ 生成中..."
    - ```generation_complete``` → "✅ 回答完成 (1.2s)"

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
        chat_model: Configured chat model instance (required for LLM generation).

    Returns:
        The final graph state dict.
    """
    checkpointer = await get_checkpointer()
    store = await get_store()  # P0: cross-session memory

    graph = build_chat_graph_compiled(
        checkpointer=checkpointer,
        store=store,
    )

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
        title="分析中...",
        markdown_text="正在分析你的问题...",
    )

    # 2. Stream: messages, updates, and custom progress (P1)
    final_answer = None
    accumulated_text = ""

    async for chunk in graph.astream(
        input_state,
        config,
        context={"db": db, "chat_model": chat_model},
        stream_mode=["messages", "updates", "custom"],
        durability="async",  # P2: async checkpoint for low-latency streaming
    ):
        if chunk["type"] == "messages":
            # Token-by-token LLM output → card typing effect
            msg_data = chunk["data"]
            if hasattr(msg_data[0], "content"):
                accumulated_text += msg_data[0].content or ""
                await card_sender.streaming_update(card_id, accumulated_text)

        elif chunk["type"] == "updates":
            data = chunk["data"]
            for node_name, node_output in data.items():
                if isinstance(node_output, dict) and "answer_dict" in node_output:
                    final_answer = node_output["answer_dict"]

        # P1: Custom stream — log-only progress tracking
        #     Card content is updated exclusively by the messages handler
        #     to avoid race conditions on the DingTalk streaming API.
        elif chunk["type"] == "custom":
            custom_data = chunk["data"]
            if isinstance(custom_data, dict):
                phase = custom_data.get("phase", "")
                if phase:
                    logger.debug(
                        "Graph progress: phase=%s task=%s sources=%s latency=%s",
                        phase,
                        custom_data.get("task_type", ""),
                        custom_data.get("source_count", ""),
                        custom_data.get("latency_ms", ""),
                    )

    # 3. Finalize card
    if final_answer is None:
        final_answer = {"summary": accumulated_text, "sections": []}

    await card_sender.streaming_finalize(card_id, accumulated_text)

    return {"answer_dict": final_answer}
