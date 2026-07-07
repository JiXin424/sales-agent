"""Fast-command handler node: help, reset, and similar instant-reply commands.

Replicates the fast-command logic from ChatPipeline.execute() steps 1-2.

P3: ``REMOVE_ALL_MESSAGES`` clears the conversation history for
     "new topic" / reset commands, instead of just skipping it.
"""

from __future__ import annotations

from langgraph.graph.message import REMOVE_ALL_MESSAGES

from sales_agent.models.base import generate_id
from sales_agent.graph.chat.state import ChatGraphState

_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}

HELP_TEXT = (
    "你可以直接问销售问题，例如：\n"
    "1. 客户说太贵了怎么回？\n"
    "2. 帮我写一段温和跟进话术\n"
    "3. 帮我复盘这段聊天记录\n\n"
    '发送"新话题"可以清空当前上下文。'
)

RESET_TEXT = "已开启新话题。你可以直接说当前要处理的销售问题。"


def fast_command_node(state: ChatGraphState) -> dict:
    """Handle help and reset commands, returning an immediate reply.

    This node is only reached when ``is_fast_command()`` returns "fast".
    It sets ``answer_dict`` and ``path`` directly, skipping all downstream nodes.

    P3: Reset commands now emit ``REMOVE_ALL_MESSAGES`` to fully clear
         the *messages* list in state (the checkpointer's saved history).
         Previous behavior only changed ``conversation_id``.

    Args:
        state: Current graph state.

    Returns:
        Partial state update.
    """
    stripped = state["message"].strip()

    if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
        return {
            "answer_dict": {"summary": HELP_TEXT, "sections": []},
            "path": "fast",
            "path_reason": "help_command",
        }

    # P3: Reset — clear messages history via REMOVE_ALL_MESSAGES
    return {
        "answer_dict": {"summary": RESET_TEXT, "sections": []},
        "path": "fast",
        "path_reason": "reset_command",
        "conversation_id": generate_id(),
        # REMOVE_ALL_MESSAGES tells the add_messages reducer to clear
        # the list completely — no stale history carried forward.
        "history_messages": REMOVE_ALL_MESSAGES,
    }
