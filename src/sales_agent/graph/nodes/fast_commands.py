"""Fast-command handler node: help, reset, and similar instant-reply commands.

Replicates the fast-command logic from ChatPipeline.execute() steps 1-2.
"""

from __future__ import annotations

from sales_agent.models.base import generate_id
from sales_agent.graph.state import ChatGraphState

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

    This node is only reached when `is_fast_command()` returns "fast".
    It sets `answer_dict` and `path` directly, skipping all downstream nodes.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with `answer_dict`, `path`, `path_reason`,
        and optionally a new `conversation_id` (for reset).
    """
    stripped = state["message"].strip()

    if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
        return {
            "answer_dict": {"summary": HELP_TEXT, "sections": []},
            "path": "fast",
            "path_reason": "help_command",
        }

    # Must be a reset command at this point (guaranteed by is_fast_command)
    return {
        "answer_dict": {"summary": RESET_TEXT, "sections": []},
        "path": "fast",
        "path_reason": "reset_command",
        "conversation_id": generate_id(),
    }
