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

from sales_agent.core.config import get_settings
from sales_agent.graph.checkpoints import get_checkpointer
from sales_agent.integrations.dingtalk.citation import format_citation_block
from sales_agent.services.online_conversation import get_online_graph
from sales_agent.services.response_formatter import format_text_output

logger = logging.getLogger(__name__)


async def handle_dingtalk_stream_via_graph(
    *,
    tenant_id: str,
    user_id: str,
    dingtalk_user_id: str,
    message: str,
    conversation_id: str,
    agent_id: str | None,
    event_id: str | None = None,
    reply_fn: Callable[[str], Awaitable[None]],
    card_sender,  # DingTalkCardSender
    db,  # AsyncSession
    chat_model,  # ChatModel instance (required for LLM generation)
    embedding_model=None,  # EmbeddingModel instance (required for RAG retrieval)
) -> dict:
    """Process a DingTalk stream message through the Online Conversation Graph.

    The graph routes to either the Guided Flow subgraph or the Chat Graph
    and handles all pipeline stages.  This function only manages the
    DingTalk card lifecycle:

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
        event_id: Event identifier for deduplication.
        reply_fn: Callback to send DingTalk messages (fallback).
        card_sender: DingTalkCardSender instance for interactive cards.
        db: Async SQLAlchemy session.
        chat_model: Configured chat model instance (required for LLM generation).

    Returns:
        The final graph state dict.
    """
    checkpointer = await get_checkpointer()
    settings = get_settings()

    graph = get_online_graph(checkpointer=checkpointer)

    thread_id = conversation_id or str(uuid.uuid4())
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    input_state = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "session_user_id": dingtalk_user_id,
        "message": message,
        "conversation_id": conversation_id,
        "channel": "dingtalk",
        "entry_action": None,
        "event_id": event_id,
        "guided_flows_enabled": settings.guided_flows.enabled,
        "topic_routing_enabled": settings.topic_routing.enabled,
        "scenario_coach_enabled": settings.scenario_coach.enabled,
        "answer_dict": None,       # clear previous turn's answer
        "response_kind": None,     # clear previous turn's routing kind (scenario_coach)
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
    chunk_count = 0

    async for chunk in graph.astream(
        input_state,
        config,
        context={"db": db, "chat_model": chat_model, "embedding_model": embedding_model},
        stream_mode=["messages", "updates", "custom"]
    ):
        chunk_count += 1
        if chunk_count <= 3:
            logger.warning(
                "GRAPH_CHUNK #%s type=%s keys=%s",
                chunk_count,
                type(chunk).__name__,
                list(chunk[1].keys()) if isinstance(chunk, tuple) and isinstance(chunk[1], dict) else "N/A",
            )
        # LangGraph ≥1.2 对 list stream_mode 返回 tuple(mode, payload)；
        # 兼容 dict 格式(旧版/单 mode 场景)。见 lessons #21。
        if isinstance(chunk, tuple) and len(chunk) >= 2:
            mode = chunk[0]
            data = chunk[1]
        elif isinstance(chunk, dict):
            mode = chunk.get("type", "")
            data = chunk.get("data")
        else:
            continue

        if mode == "messages":
            # Token-by-token LLM output → card typing effect
            if hasattr(data[0], "content"):
                accumulated_text += data[0].content or ""
                await card_sender.streaming_update(card_id, accumulated_text)

        elif mode == "updates":
            for node_name, node_output in data.items():
                if isinstance(node_output, dict) and "answer_dict" in node_output:
                    final_answer = node_output["answer_dict"]

        # P1: Custom stream — log-only progress tracking
        #     Card content is updated exclusively by the messages handler
        #     to avoid race conditions on the DingTalk streaming API.
        elif mode == "custom":
            if isinstance(data, dict):
                phase = data.get("phase", "")
                if phase:
                    logger.debug(
                        "Graph progress: phase=%s task=%s sources=%s latency=%s",
                        phase,
                        data.get("task_type", ""),
                        data.get("source_count", ""),
                        data.get("latency_ms", ""),
                    )

    answer_summary = final_answer.get("summary", "") if final_answer else ""
    logger.warning(
        "GRAPH_DONE: chunks=%s answer_keys=%s acc_len=%s summary_preview=%s",
        chunk_count,
        list(final_answer.keys()) if final_answer else "None",
        len(accumulated_text),
        answer_summary[:200] if answer_summary else "(empty)",
    )
    # 3. Finalize card — use updates answer if streaming didn't capture text
    if final_answer is None:
        final_answer = {"summary": accumulated_text, "sections": []}

    display_text = accumulated_text
    if not display_text and final_answer:
        # Non-streamed structured answer (e.g. scenario_coach preset hit, which
        # sets answer_dict without any LLM token stream): render the full
        # summary + sections the same way the HTTP path does, instead of the
        # old summary-then-sections fallback that dropped sections whenever a
        # summary was present.
        display_text = format_text_output(final_answer)

    # 方案 A：正文确定后，把来源列表拼到末尾（不走 streaming，避免卡片闪烁）
    if display_text and final_answer:
        citation = format_citation_block(final_answer.get("sources", []))
        if citation:
            display_text += citation

    logger.warning("CARD_FINALIZE: display_len=%s", len(display_text))
    await card_sender.streaming_finalize(card_id, display_text)

    return {"answer_dict": final_answer}
