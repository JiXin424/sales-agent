"""钉钉命令解析单元测试。"""

from sales_agent.integrations.dingtalk.command_parser import DingTalkCommandParser, ParsedCommand


class TestDingTalkCommandParser:

    def setup_method(self):
        self.parser = DingTalkCommandParser()

    # --- Reset 命令 ---

    def test_reset_command_新话题(self):
        result = self.parser.parse("新话题")
        assert result.is_reset is True
        assert result.remaining_message == ""

    def test_reset_command_清空上下文(self):
        result = self.parser.parse("清空上下文")
        assert result.is_reset is True

    def test_reset_command_重新开始(self):
        result = self.parser.parse("重新开始")
        assert result.is_reset is True

    def test_reset_command_忘掉前面(self):
        result = self.parser.parse("忘掉前面")
        assert result.is_reset is True

    def test_reset_command_slash_reset(self):
        result = self.parser.parse("/reset")
        assert result.is_reset is True

    def test_reset_command_slash_new(self):
        result = self.parser.parse("/new")
        assert result.is_reset is True

    def test_reset_command_with_remaining_message_comma(self):
        result = self.parser.parse("新话题，帮我写一段初次触达话术")
        assert result.is_reset is True
        assert result.remaining_message == "帮我写一段初次触达话术"

    def test_reset_command_with_remaining_message_space(self):
        result = self.parser.parse("新话题 帮我写话术")
        assert result.is_reset is True
        assert result.remaining_message == "帮我写话术"

    # --- Help 命令 ---

    def test_help_command_帮助(self):
        result = self.parser.parse("帮助")
        assert result.is_help is True

    def test_help_command_slash_help(self):
        result = self.parser.parse("/help")
        assert result.is_help is True

    # --- 普通消息 ---

    def test_normal_message(self):
        result = self.parser.parse("客户说太贵了怎么回？")
        assert result.is_reset is False
        assert result.is_help is False
        assert result.remaining_message == "客户说太贵了怎么回？"

    def test_empty_message(self):
        result = self.parser.parse("")
        assert result.is_reset is False
        assert result.is_help is False

    # --- 自定义命令列表 ---

    def test_custom_reset_commands(self):
        parser = DingTalkCommandParser(reset_commands=["start", "restart"])
        result = parser.parse("start")
        assert result.is_reset is True

    def test_reset_priority_over_help(self):
        # 如果 "新话题" 不是 help 命令，reset 应该优先
        result = self.parser.parse("新话题")
        assert result.is_reset is True
        assert result.is_help is False
