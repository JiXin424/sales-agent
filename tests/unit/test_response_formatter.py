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

    def test_group_header_without_content(self):
        # 分组父标题（如「二、再应对」）自身无独立正文、其内容是后续 #### 子节，
        # 应作为纯标题行输出，不能因 content 为空被丢弃。
        answer = {
            "summary": "前言",
            "sections": [
                {"title": "二、再应对：判断完，对应着打", "content": ""},
                {"title": "预算 / 流程没走完", "content": "- **方向**：锁方案锁价格"},
            ],
        }
        text = format_text_output(answer)
        assert "二、再应对：判断完，对应着打" in text
        assert "预算 / 流程没走完" in text


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
