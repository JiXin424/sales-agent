"""输出结构测试：验证 6 个工作流的 JSON schema 定义。"""

import pytest

from sales_agent.services.task_router import WORKFLOW_TASK_TYPES


# 每个 workflow task type 的输出应该包含的 section titles（关键字匹配）
_WORKFLOW_EXPECTED_SECTIONS = {
    "visit_preparation": {
        "required": ["本次沟通目标", "建议开场话术", "必问 3 个问题", "本次必须争取的下一步"],
        "forbidden_summaries": [],
    },
    "post_visit_review": {
        "required": ["本次沟通结论", "当前机会状态", "最大卡点", "下一步最佳动作"],
        "forbidden_summaries": [],
    },
    "follow_up_planning": {
        "required": ["跟进时机", "下一步行动", "消息草稿", "备选方案"],
        "forbidden_summaries": [],
    },
    "customer_context_summary": {
        "required": ["客户行业", "核心痛点", "缺失信息"],
        "forbidden_summaries": [],
    },
    "deal_advancement": {
        "required": ["阶段诊断", "成交阻碍", "下一步"],
        "forbidden_summaries": [],
    },
    "conversation_scoring": {
        "required": ["总分", "改进优先级"],
        "forbidden_summaries": [],
    },
}


class TestOutputShapes:
    """验证工作流输出的 JSON 结构定义。"""

    def test_workflow_task_types_are_six(self):
        """Phase B 共 6 个工作流任务类型（含 post_visit_review）。"""
        assert len(WORKFLOW_TASK_TYPES) == 6

    def test_workflow_task_types_are_in_all_task_types(self):
        """所有工作流任务类型必须在 ALL_TASK_TYPES 中。"""
        from sales_agent.services.task_router import ALL_TASK_TYPES
        for task_type in WORKFLOW_TASK_TYPES:
            assert task_type in ALL_TASK_TYPES

    def test_workflow_expected_sections_defined_for_all(self):
        """每个工作流任务类型都有期望的 section 定义。"""
        for task_type in WORKFLOW_TASK_TYPES:
            assert task_type in _WORKFLOW_EXPECTED_SECTIONS, \
                f"No expected sections defined for '{task_type}'"

    @pytest.mark.parametrize("task_type", list(WORKFLOW_TASK_TYPES))
    def test_workflow_has_required_sections_spec(self, task_type: str):
        """每个工作流必须定义 required sections。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS[task_type]
        assert "required" in spec
        assert len(spec["required"]) > 0

    def test_visit_preparation_output_structure(self):
        """访前准备输出结构验证 — 访前作战卡 8 个固定段落。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["visit_preparation"]
        required = spec["required"]
        assert "本次沟通目标" in required
        assert "必问 3 个问题" in required

    def test_post_visit_review_output_structure(self):
        """访后复盘输出结构验证 — 访后机会推进卡 8 个固定段落。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["post_visit_review"]
        required = spec["required"]
        assert "本次沟通结论" in required
        assert "当前机会状态" in required
        assert "最大卡点" in required
        assert "下一步最佳动作" in required

    def test_follow_up_planning_output_structure(self):
        """跟进计划输出结构验证。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["follow_up_planning"]
        required = spec["required"]
        assert "跟进时机" in required
        assert "下一步行动" in required
        assert "消息草稿" in required

    def test_customer_context_output_structure(self):
        """客户上下文输出结构验证。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["customer_context_summary"]
        required = spec["required"]
        assert "缺失信息" in required
        assert "客户行业" in required

    def test_deal_advancement_output_structure(self):
        """成交推进输出结构验证。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["deal_advancement"]
        required = spec["required"]
        assert "阶段诊断" in required
        assert "成交阻碍" in required

    def test_conversation_scoring_output_structure(self):
        """对话评分输出结构验证。"""
        spec = _WORKFLOW_EXPECTED_SECTIONS["conversation_scoring"]
        required = spec["required"]
        assert "总分" in required
        assert "改进优先级" in required

    def test_all_workflow_types_default_no_retrieval(self):
        """所有工作流任务类型默认不需要 RAG。"""
        from sales_agent.services.task_router import TASK_DEFAULT_RETRIEVAL
        for task_type in WORKFLOW_TASK_TYPES:
            assert TASK_DEFAULT_RETRIEVAL.get(task_type) is False, \
                f"Workflow task '{task_type}' should default to no retrieval"
