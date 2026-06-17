"""钉钉流式渲染单元测试。

重点：流式过程中 LLM 输出的是（可能不完整的）JSON，`_render_streaming_text`
必须渲染成"人读"的 Markdown，**绝不能**把原始 JSON 脚手架
（`summary`/`sections`/`title`/`content` 键名）泄露给用户——否则用户会看到
类似"思考过程"的内部结构。
"""

from sales_agent.integrations.dingtalk.streaming_handler import _render_streaming_text


class TestRenderStreamingText:
    # ------------------------------------------------------------------
    # 回归保护：完全 JSON 仍需正确渲染
    # ------------------------------------------------------------------
    def test_complete_json_renders_nicely(self):
        raw = (
            '{"summary": "化解价格异议", "sections": ['
            '{"title": "共情确认", "content": "理解您的感受"}, '
            '{"title": "价值对比", "content": "我们的价值在于..."}'
            "]}"
        )
        out = _render_streaming_text(raw)
        assert "化解价格异议" in out
        assert "共情确认" in out
        assert "价值对比" in out
        assert "我们的价值在于" in out

    def test_code_fence_wrapped_json(self):
        raw = '```json\n{"summary": "测试", "sections": []}\n```'
        out = _render_streaming_text(raw)
        assert "测试" in out
        assert "```" not in out

    # ------------------------------------------------------------------
    # 核心 bug：不完整 JSON 不得泄露原始结构
    # ------------------------------------------------------------------
    def test_partial_summary_does_not_leak_raw_json(self):
        raw = '{"summary": "通过共情、价值对比'
        out = _render_streaming_text(raw)
        # 渲染成可读的粗体摘要，带流式光标
        assert "通过共情" in out
        assert out.lstrip().startswith("**")
        # 绝不出现 JSON 键名 / 大括号
        assert "summary" not in out
        assert "{" not in out
        assert '"' not in out

    def test_partial_section_does_not_leak_raw_json(self):
        raw = (
            '{"summary": "化解异议", "sections": ['
            '{"title": "共情确认", "content": "理解您的感受'
        )
        out = _render_streaming_text(raw)
        assert "化解异议" in out
        assert "共情确认" in out
        assert "理解您的感受" in out
        assert "summary" not in out
        assert "sections" not in out
        assert "title" not in out
        assert "content" not in out
        assert "{" not in out

    def test_empty_buffer_shows_placeholder_not_raw(self):
        assert _render_streaming_text("") == _placeholder()
        # 只有 JSON 前缀、还没有任何值时也不泄露
        out = _render_streaming_text('{\n  ')
        assert out == _placeholder()
        assert "{" not in out

    # ------------------------------------------------------------------
    # 流式光标 + 转义
    # ------------------------------------------------------------------
    def test_streaming_view_has_cursor(self):
        raw = '{"summary": "思考中'
        out = _render_streaming_text(raw)
        assert "▍" in out

    def test_escaped_newline_in_content_is_unescaped(self):
        raw = '{"summary": "x", "sections": [{"title": "A", "content": "第一行\\n第二行"}]}'
        out = _render_streaming_text(raw)
        assert "第一行\n第二行" in out
        assert "\\n" not in out  # 不应残留转义序列

    # ------------------------------------------------------------------
    # 防御：<think> 标签剥离（多租户将来换推理模型时）
    # ------------------------------------------------------------------
    def test_completed_think_block_is_stripped(self):
        raw = '<think>我需要先分析价格异议</think>{"summary": "建议'
        out = _render_streaming_text(raw)
        assert "建议" in out
        assert "<think>" not in out
        assert "分析价格异议" not in out  # 思考内容不展示

    def test_dangling_think_is_hidden_until_content_starts(self):
        # 模型还在思考（<think> 未闭合）——此时不应泄露思考内容，显示占位
        raw = '<think>正在推理价格异议的根因'
        out = _render_streaming_text(raw)
        assert out == _placeholder()
        assert "推理价格异议" not in out


def _placeholder() -> str:
    """与实现保持一致的占位文案（独立常量避免循环导入）。"""
    from sales_agent.integrations.dingtalk.streaming_handler import (
        _STREAMING_PLACEHOLDER,
    )

    return _STREAMING_PLACEHOLDER
