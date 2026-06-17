"""钉钉命令解析 — 解析 reset、help 等文本命令。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedCommand:
    """命令解析结果。"""

    is_reset: bool = False
    is_help: bool = False
    remaining_message: str = ""


class DingTalkCommandParser:
    """钉钉消息命令解析器。

    优先级：
    1. reset 命令（新话题、清空上下文等）
    2. help 命令
    3. 连续优化指令
    4. 普通消息
    """

    RESET_COMMANDS: set[str] = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}
    HELP_COMMANDS: set[str] = {"帮助", "/help"}
    # 分隔符：用户可能在命令后加逗号或空格再接内容
    SEPARATORS = ("，", ",", "。", "、", " ", "\n")

    def __init__(self, reset_commands: list[str] | None = None):
        if reset_commands:
            self._reset_commands = set(reset_commands)
        else:
            self._reset_commands = self.RESET_COMMANDS

    def parse(self, message: str) -> ParsedCommand:
        """解析消息中的命令。

        Args:
            message: 用户消息文本

        Returns:
            解析结果
        """
        if not message:
            return ParsedCommand()

        text = message.strip()

        # 检查 reset 命令（精确匹配 + 前缀匹配）
        for cmd in self._reset_commands:
            if text == cmd:
                return ParsedCommand(is_reset=True)
            if text.startswith(cmd):
                rest = text[len(cmd):]
                # 检查命令后面是否紧跟分隔符
                if rest and rest[0] in self.SEPARATORS:
                    remaining = rest.lstrip("".join(self.SEPARATORS))
                    return ParsedCommand(is_reset=True, remaining_message=remaining)
                elif rest and not rest[0].isalnum():
                    remaining = rest.lstrip("".join(self.SEPARATORS))
                    return ParsedCommand(is_reset=True, remaining_message=remaining)

        # 检查 help 命令
        if text in self.HELP_COMMANDS:
            return ParsedCommand(is_help=True)

        return ParsedCommand(remaining_message=text)
