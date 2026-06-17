"""钉钉回复渲染单元测试。"""

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.message_renderer import DingTalkMessageRenderer


class TestDingTalkMessageRenderer:

    def _make_renderer(self, max_chars: int = 3000) -> DingTalkMessageRenderer:
        config = DingTalkConfig(enabled=True, max_reply_chars=max_chars)
        return DingTalkMessageRenderer(config)

    def test_render_normal_response(self):
        renderer = self._make_renderer()
        answer = {
            "summary": "这是价格异议",
            "sections": [
                {"title": "异议类型", "content": "价格异议"},
                {"title": "推荐话术", "content": "「我们的价值在于...」"},
            ],
        }
        result = renderer.render(answer, [])
        assert "价格异议" in result
        assert "推荐话术" in result

    def test_render_with_sources(self):
        renderer = self._make_renderer()
        answer = {"summary": "测试", "sections": []}
        sources = [
            {"title": "产品价值说明", "score": 0.9},
            {"title": "常见问题 FAQ", "score": 0.8},
        ]
        result = renderer.render(answer, sources)
        assert "产品价值说明" in result
        assert "常见问题 FAQ" in result

    def test_render_truncates_long_text(self):
        renderer = self._make_renderer(max_chars=100)
        answer = {
            "summary": "A" * 200,
            "sections": [],
        }
        result = renderer.render(answer, [])
        assert len(result) <= 120  # 允许截断提示的额外字符
        assert "截断" in result

    def test_render_error_model_failed(self):
        renderer = self._make_renderer()
        result = renderer.render_error("model_failed")
        assert "模型服务" in result

    def test_render_error_no_knowledge(self):
        renderer = self._make_renderer()
        result = renderer.render_error("no_knowledge")
        assert "知识库" in result

    def test_render_error_risk_blocked(self):
        renderer = self._make_renderer()
        result = renderer.render_error("risk_blocked")
        assert "承诺" in result

    def test_render_error_unknown_type(self):
        renderer = self._make_renderer()
        result = renderer.render_error("unknown_error")
        assert "无法处理" in result
