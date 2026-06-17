"""输出标准化器测试：验证访前/访后卡片输出固定段落名称和顺序。"""

import pytest

from sales_agent.services.task_router import VISIT_PREPARATION, POST_VISIT_REVIEW
from sales_agent.services.output_normalizer import (
    normalize_answer,
    CANONICAL_SECTIONS,
    PRE_VISIT_CANONICAL,
    POST_VISIT_CANONICAL,
    FALLBACK_CONTENT,
)


class TestOutputNormalizerNoop:
    """非卡片类型应原样返回。"""

    def test_noop_for_knowledge_qa(self):
        answer = {"summary": "test", "sections": [{"title": "回答", "content": "内容"}]}
        result = normalize_answer("knowledge_qa", answer)
        assert result is answer
        assert result == answer

    def test_noop_for_emotional_support(self):
        answer = {"summary": "test", "sections": []}
        result = normalize_answer("emotional_support", answer)
        assert result is answer

    def test_noop_for_general_coaching(self):
        answer = {"summary": "test", "sections": [{"title": "回答", "content": "内容"}]}
        result = normalize_answer("general_sales_coaching", answer)
        assert result is answer


class TestPreVisitNormalization:
    """访前作战卡标准化测试。"""

    def _make_answer(self, sections):
        return {"summary": "访前作战卡 | 测试", "sections": sections}

    def test_exact_match_no_change(self):
        """已规范的输出，段落顺序和内容不变。"""
        sections = [
            {"title": t, "content": f"内容_{i}"}
            for i, t in enumerate(PRE_VISIT_CANONICAL)
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(VISIT_PREPARATION, answer)

        result_titles = [s["title"] for s in result["sections"]]
        assert result_titles == PRE_VISIT_CANONICAL
        assert result["card_type"] == VISIT_PREPARATION

    def test_reorder_pre_visit_sections(self):
        """乱序的段落应该被重新排列为规范顺序。"""
        sections = [
            {"title": "最大风险提醒", "content": "风险内容"},
            {"title": "本次沟通目标", "content": "目标内容"},
            {"title": "建议开场话术", "content": "开场内容"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(VISIT_PREPARATION, answer)

        result_titles = [s["title"] for s in result["sections"][:8]]
        assert result_titles[0] == "本次沟通目标"
        assert result_titles[1] == "客户可能关心的 3 件事"
        assert result_titles[2] == "建议开场话术"

    def test_alias_mapping(self):
        """模型使用别名应映射为规范标题。"""
        sections = [
            {"title": "拜访目标", "content": "目标内容"},
            {"title": "探询问题", "content": "问题内容"},
            {"title": "开场话术", "content": "开场内容"},
            {"title": "收口话术", "content": "收口内容"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(VISIT_PREPARATION, answer)

        result_titles = [s["title"] for s in result["sections"]]
        assert "本次沟通目标" in result_titles
        assert "必问 3 个问题" in result_titles
        assert "建议开场话术" in result_titles
        assert "可直接使用的收口话术" in result_titles

    def test_missing_section_gets_placeholder(self):
        """缺失的规范段落应插入兜底内容。"""
        sections = [
            {"title": "本次沟通目标", "content": "目标内容"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(VISIT_PREPARATION, answer)

        # 应该有 8 个规范段落
        assert len(result["sections"]) >= 8

        # 缺失段落用兜底内容
        for section in result["sections"][1:]:
            if section["content"] == FALLBACK_CONTENT:
                break
        else:
            pytest.fail("Expected at least one section with fallback content")

    def test_extra_sections_appended(self):
        """模型额外产生的段落应追加到末尾。"""
        sections = [
            {"title": t, "content": f"内容_{i}"}
            for i, t in enumerate(PRE_VISIT_CANONICAL)
        ]
        sections.append({"title": "客户行业分析", "content": "额外分析内容"})
        answer = self._make_answer(sections)
        result = normalize_answer(VISIT_PREPARATION, answer)

        # 最后一个应该是"补充建议"
        last = result["sections"][-1]
        assert last["title"] == "补充建议"
        assert "额外分析内容" in last["content"]


class TestPostVisitNormalization:
    """访后机会推进卡标准化测试。"""

    def _make_answer(self, sections):
        return {"summary": "访后机会推进卡 | 测试", "sections": sections}

    def test_post_visit_full_normalization(self):
        """完整的访后卡片标准化。"""
        sections = [
            {"title": "本次沟通结论", "content": "客户表达了对产品的兴趣"},
            {"title": "客户真实需求判断", "content": "客户需要提升效率"},
            {"title": "当前机会状态", "content": "B 需培育\n理由：预算未批"},
            {"title": "最大卡点", "content": "预算审批流程慢"},
            {"title": "下一步最佳动作", "content": "3天内发送方案"},
            {"title": "可直接发送的跟进话术", "content": "「王总，方案已准备好...」"},
            {"title": "是否需要管理者介入", "content": "不需要\n理由：正常推进中"},
            {"title": "下次复盘观察点", "content": "1. 客户是否反馈方案\n2. 预算审批进度"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(POST_VISIT_REVIEW, answer)

        result_titles = [s["title"] for s in result["sections"][:8]]
        assert result_titles == POST_VISIT_CANONICAL
        assert result["card_type"] == POST_VISIT_REVIEW

    def test_post_visit_alias_mapping(self):
        """访后卡片别名映射。"""
        sections = [
            {"title": "结论", "content": "沟通结论内容"},
            {"title": "真实需求", "content": "需求内容"},
            {"title": "机会状态", "content": "A 可推进"},
            {"title": "卡点", "content": "卡点内容"},
            {"title": "下一步动作", "content": "动作内容"},
            {"title": "跟进话术", "content": "话术内容"},
            {"title": "管理者", "content": "需要"},
            {"title": "观察点", "content": "观察内容"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(POST_VISIT_REVIEW, answer)

        result_titles = [s["title"] for s in result["sections"][:8]]
        assert result_titles == POST_VISIT_CANONICAL

    def test_opportunity_status_preserved(self):
        """A/B/C/D 机会状态在标准化后保留。"""
        sections = [
            {"title": "当前机会状态", "content": "C 高风险\n理由：竞品介入"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(POST_VISIT_REVIEW, answer)

        # 找到机会状态段落
        status_section = next(
            s for s in result["sections"] if s["title"] == "当前机会状态"
        )
        assert "C" in status_section["content"]
        assert "高风险" in status_section["content"]

    def test_duplicate_section_keeps_longer_content(self):
        """重复段落保留内容更长的版本。"""
        sections = [
            {"title": "本次沟通结论", "content": "短内容"},
            {"title": "本次沟通结论", "content": "这是一段更长的沟通结论内容，包含更多细节"},
        ]
        answer = self._make_answer(sections)
        result = normalize_answer(POST_VISIT_REVIEW, answer)

        conclusion = next(
            s for s in result["sections"] if s["title"] == "本次沟通结论"
        )
        assert "更长的" in conclusion["content"]


class TestCardTypeField:
    """card_type 字段测试。"""

    def test_pre_visit_sets_card_type(self):
        answer = {"summary": "test", "sections": []}
        result = normalize_answer(VISIT_PREPARATION, answer)
        assert result["card_type"] == VISIT_PREPARATION

    def test_post_visit_sets_card_type(self):
        answer = {"summary": "test", "sections": []}
        result = normalize_answer(POST_VISIT_REVIEW, answer)
        assert result["card_type"] == POST_VISIT_REVIEW

    def test_non_card_type_no_card_type(self):
        answer = {"summary": "test", "sections": []}
        result = normalize_answer("knowledge_qa", answer)
        assert "card_type" not in result


class TestCanonicalSectionsDefinition:
    """规范段落定义完整性测试。"""

    def test_pre_visit_has_eight_sections(self):
        assert len(PRE_VISIT_CANONICAL) == 8

    def test_post_visit_has_eight_sections(self):
        assert len(POST_VISIT_CANONICAL) == 8

    def test_canonical_sections_covers_both_card_types(self):
        assert VISIT_PREPARATION in CANONICAL_SECTIONS
        assert POST_VISIT_REVIEW in CANONICAL_SECTIONS
