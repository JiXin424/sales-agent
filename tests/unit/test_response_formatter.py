"""Response Formatter 单元测试。"""

from sales_agent.services.response_formatter import (
    format_text_output,
    format_sales_visible_sources,
    build_chat_response,
)


class TestFormatTextOutput:
    def test_basic_format(self):
        answer = {
            "summary": "这是价格异议",
            "sections": [
                {"title": "异议类型", "content": "价格异议"},
                {"title": "建议策略", "content": "先澄清比较口径"},
            ],
        }
        text = format_text_output(answer)
        assert "这是价格异议" in text
        assert "异议类型" in text
        assert "价格异议" in text
        assert "建议策略" in text

    def test_empty_answer(self):
        text = format_text_output({})
        assert text == ""

    def test_no_sections(self):
        text = format_text_output({"summary": "测试"})
        assert "测试" in text


class TestFormatSources:
    def test_basic_sources(self):
        sources = [
            {"title": "产品介绍", "display_title": "产品介绍"},
            {"title": "FAQ", "display_title": "常见问题"},
        ]
        text = format_sales_visible_sources(sources)
        assert "《产品介绍》" in text
        assert "《常见问题》" in text

    def test_max_visible(self):
        sources = [
            {"title": f"文档{i}", "display_title": f"文档{i}"}
            for i in range(5)
        ]
        text = format_sales_visible_sources(sources, max_visible=3)
        assert "《文档0》" in text
        assert "共 5 条来源" in text

    def test_empty_sources(self):
        text = format_sales_visible_sources([])
        assert text == ""


class TestBuildChatResponse:
    def test_basic_response(self):
        resp = build_chat_response(
            conversation_id="conv_1",
            tenant_id="t1",
            task_type="objection_handling",
            answer={"summary": "test", "sections": []},
        )
        assert resp["conversation_id"] == "conv_1"
        assert resp["tenant_id"] == "t1"
        assert resp["task_type"] == "objection_handling"
        assert resp["answer"]["summary"] == "test"

    def test_with_sources_and_risk(self):
        resp = build_chat_response(
            conversation_id="conv_1",
            tenant_id="t1",
            task_type="knowledge_qa",
            answer={"summary": "基于知识库"},
            sources=[{"title": "文档1", "score": 0.9}],
            risk={"level": "none", "action": "allow"},
        )
        assert len(resp["sources"]) == 1
        assert resp["risk"]["level"] == "none"
