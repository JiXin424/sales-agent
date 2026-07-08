"""Tests for scenario_coach citation label."""

from __future__ import annotations

from sales_agent.integrations.dingtalk.citation import (
    format_citation_block,
    source_type_label,
)


def test_scenario_coach_label():
    assert source_type_label("scenario_coach") == "教练手册"


def test_scenario_coach_citation_block():
    sources = [
        {
            "title": "销冠智慧教练手册·2026年4月版",
            "display_title": "销冠智慧教练手册·2026年4月版",
            "source_type": "scenario_coach",
        }
    ]
    block = format_citation_block(sources)
    assert "销冠智慧教练手册·2026年4月版" in block
    assert "教练手册" in block
    assert "引用来源" in block


def test_existing_labels_unchanged():
    assert source_type_label("ontology") == "知识图谱"
    assert source_type_label("web") == "网络搜索"
    assert source_type_label(None) == "知识库"
    assert source_type_label("rag") == "知识库"
