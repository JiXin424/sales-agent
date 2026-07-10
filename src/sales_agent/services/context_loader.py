"""会话上下文辅助：重置命令检测。

历史的三层上下文加载（``load_context`` / ``maybe_update_summary``）已迁移到
Online Graph 的 ``context_load`` / ``context_resolution`` 节点；本模块仅保留
``is_reset_command`` 供 CLI 使用。
"""

from __future__ import annotations

from sales_agent.core.config import get_settings


def is_reset_command(message: str, reset_commands: list[str] | None = None) -> bool:
    """检测用户是否要重置上下文。"""
    commands = reset_commands or get_settings().conversation.reset_commands
    stripped = message.strip()
    return stripped in commands
