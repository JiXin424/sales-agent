"""钉钉回答末尾引用来源格式化测试。"""
from sales_agent.integrations.dingtalk.citation import (
    format_citation_block,
    source_type_label,
)


def test_source_type_label():
    assert source_type_label("ontology") == "知识图谱"
    assert source_type_label("web") == "网络搜索"
    assert source_type_label("product_doc") == "知识库"
    assert source_type_label("faq") == "知识库"
    assert source_type_label("") == "知识库"
    assert source_type_label(None) == "知识库"


def test_empty_sources_returns_empty():
    assert format_citation_block(None) == ""
    assert format_citation_block([]) == ""


def test_ontology_sources_labeled_knowledge_graph():
    sources = [
        {"title": "东方福利网竞品分析", "source_type": "ontology"},
        {"title": "幸福加焙对比", "source_type": "ontology"},
    ]
    out = format_citation_block(sources)
    assert "📖 引用来源" in out
    assert "[1] 东方福利网竞品分析 · 知识图谱" in out
    assert "[2] 幸福加焙对比 · 知识图谱" in out


def test_rag_sources_labeled_knowledge_base():
    sources = [{"title": "零风险承诺", "source_type": "product_doc"}]
    out = format_citation_block(sources)
    assert "[1] 零风险承诺 · 知识库" in out


def test_web_sources_labeled_web_search():
    sources = [{"title": "博查结果", "source_type": "web"}]
    out = format_citation_block(sources)
    assert "[1] 博查结果 · 网络搜索" in out


def test_web_search_source_type_labeled_web_search():
    """web_fallback_and_analyze emits source_type='web_search' (not 'web')."""
    sources = [{"title": "阿根廷VS埃及", "source_type": "web_search"}]
    out = format_citation_block(sources)
    assert "[1] 阿根廷VS埃及 · 网络搜索" in out


def test_dedup_by_title():
    sources = [
        {"title": "同名文档", "source_type": "ontology"},
        {"title": "同名文档", "source_type": "ontology"},
        {"title": "另一份", "source_type": "ontology"},
    ]
    out = format_citation_block(sources)
    assert out.count("同名文档") == 1
    assert "[2] 另一份" in out


def test_max_three_sources():
    sources = [{"title": f"文档{i}", "source_type": "ontology"} for i in range(5)]
    out = format_citation_block(sources)
    assert "[1]" in out and "[3]" in out
    assert "[4]" not in out


def test_missing_title_fallback():
    sources = [{"source_type": "web"}]  # 无 title
    out = format_citation_block(sources)
    assert "[1] 网络搜索来源" in out


def test_display_title_preferred():
    sources = [{"title": "短", "display_title": "完整显示标题", "source_type": "ontology"}]
    out = format_citation_block(sources)
    assert "完整显示标题" in out
    assert "短" not in out  # 不用短 title


def test_citation_header_banner_and_line_breaks():
    """Header 是横幅样式；每条引用用空行分隔以在钉钉卡片里换行。"""
    sources = [
        {"title": "文档A", "source_type": "ontology"},
        {"title": "文档B", "source_type": "web_search"},
    ]
    out = format_citation_block(sources)
    assert "────📖 引用来源─────" in out
    # 紧凑换行：条目间用 <br>（钉钉卡片 markdown 单 \n 折叠、\n\n 留空行）
    assert "[1] 文档A · 知识图谱<br>[2] 文档B · 网络搜索" in out
