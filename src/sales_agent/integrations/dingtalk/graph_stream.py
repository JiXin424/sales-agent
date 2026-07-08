"""DingTalk streaming via LangGraph ``astream()``.

Replaces ``streaming_handler.py``'s duplicated pipeline logic.
The graph handles all pipeline stages; this module only manages
the DingTalk interactive card lifecycle.

Task 6: shares ``prepare_online_turn`` with the standard ``invoke_online_turn``
path so streaming and standard turns use the SAME thread ID, input state,
config, context, and Graph. Neither builds its own thread/input.

P1: Custom stream events from nodes (via ``runtime.stream_writer``)
     drive the card progress display: retrieval progress → generation
     progress → done. The card shows clear phase transitions instead
     of a single "thinking..." spinner.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sales_agent.integrations.dingtalk.citation import format_citation_block
from sales_agent.core.config import get_settings
from sales_agent.services.online_conversation import get_online_graph
from sales_agent.services.response_formatter import format_text_output
from sales_agent.services.online_conversation import (
    acquire_online_turn_lock,
    prepare_online_turn,
)

logger = logging.getLogger()


async def update_dingtalk_card_from_graph_chunk(
    *,
    chunk,
    card_id: str,
    card_sender,
) -> None:
    """Parse one ``astream`` chunk and update the DingTalk interactive card.

    Handles three LangGraph stream modes (returned as a ``(mode, payload)``
    tuple when multiple modes are requested):

    - ``messages`` — token-by-token LLM output → card streaming update.
    - ``updates`` — node completion → extract the final ``answer_dict``.
    - ``custom`` — progress events from nodes → log-only (card content is
      driven exclusively by the ``messages`` handler to avoid races on the
      DingTalk streaming API).

    Keeps only interactive-card progress/rendering logic in this module.
    """
    # LangGraph ≥1.2 对 list stream_mode 返回 tuple(mode, payload)；
    # 兼容 dict 格式(旧版/单 mode 场景)。见 lessons #21。
    if isinstance(chunk, tuple) and len(chunk) >= 2:
        mode = chunk[0]
        data = chunk[1]
    elif isinstance(chunk, dict):
        mode = chunk.get("type", "")
        data = chunk.get("data")
    else:
        return

    if mode == "messages":
        # Token-by-token LLM output → card typing effect
        if hasattr(data[0], "content"):
            await card_sender.streaming_update(
                card_id, getattr(card_sender, "_streaming_text", "") + (data[0].content or ""),
            )

    elif mode == "updates":
        for _node_name, node_output in data.items():
            if isinstance(node_output, dict) and "answer_dict" in node_output:
                # Stash the latest final answer on the card_sender so the
                # caller can finalize the card after the stream completes.
                card_sender._streaming_final_answer = node_output["answer_dict"]

    # P1: Custom stream — log-only progress tracking.
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

    Task 6: delegates thread ID / input / config / context / graph to
    ``prepare_online_turn`` (shared with ``invoke_online_turn``) and
    acquires ``acquire_online_turn_lock`` before streaming so the standard
    and streaming paths are parity-identical.

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
        embedding_model: Embedding model instance.

    Returns:
        The final graph state dict (with ``thread_id`` for shared identity).
    """
    settings = get_settings()
    prepared = await prepare_online_turn(
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        session_user_id=dingtalk_user_id,
        channel="dingtalk",
        conversation_id=conversation_id,
        message=message,
        entry_action=None,
        event_id=event_id,
        reset_requested=False,
        chat_model=chat_model,
        embedding_model=embedding_model,
        scenario_coach_enabled=settings.scenario_coach.enabled,
    )
    # Acquire the transaction-scoped advisory lock BEFORE streaming so a
    # concurrent worker delivering a turn for the same thread blocks until
    # this caller's transaction commits/rolls back.
    await acquire_online_turn_lock(db, prepared.thread_id)

    # 1. Create initial card
    card_id = await card_sender.send_markdown_card(
        dingtalk_user_id=dingtalk_user_id,
        title="分析中...",
        markdown_text="正在分析你的问题...",
    )

    # 2. Stream: messages, updates, and custom progress (P1)
    accumulated_text = ""
    chunk_count = 0
    final_answer = None

    # The card streaming update needs the accumulated text; reset the
    # scratch fields on card_sender so update_dingtalk_card_from_graph_chunk
    # can append to them safely.
    card_sender._streaming_text = ""
    card_sender._streaming_final_answer = None

    async for chunk in prepared.graph.astream(
        prepared.input_state,
        prepared.config,
        context=prepared.context,
        stream_mode=["messages", "updates", "custom"],
    ):
        chunk_count += 1
        if chunk_count <= 3:
            logger.warning(
                "GRAPH_CHUNK #%s type=%s keys=%s",
                chunk_count,
                type(chunk).__name__,
                list(chunk[1].keys()) if isinstance(chunk, tuple) and isinstance(chunk[1], dict) else "N/A",
            )

        # Track accumulated text from messages mode for the card + fallback.
        if isinstance(chunk, tuple) and len(chunk) >= 2 and chunk[0] == "messages":
            if hasattr(chunk[1][0], "content"):
                accumulated_text += chunk[1][0].content or ""
                card_sender._streaming_text = accumulated_text
        elif isinstance(chunk, dict) and chunk.get("type") == "messages":
            data = chunk.get("data")
            if data and hasattr(data[0], "content"):
                accumulated_text += data[0].content or ""
                card_sender._streaming_text = accumulated_text

        await update_dingtalk_card_from_graph_chunk(
            chunk=chunk,
            card_id=card_id,
            card_sender=card_sender,
        )

    final_answer = getattr(card_sender, "_streaming_final_answer", None)

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

    # Share the thread_id identity with the standard path so evaluation
    # traces and DingTalkTurnResult see the same observable identity.
    return {"answer_dict": final_answer, "thread_id": prepared.thread_id}
