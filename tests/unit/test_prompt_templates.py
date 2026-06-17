"""Prompt 模板测试：验证所有 prompt 包含必要占位符和基本结构。"""

import re
import pytest

from sales_agent.services.task_router import ALL_TASK_TYPES
from sales_agent.services.agent_executor import _TASK_PROMPTS
from sales_agent.services.prompt_registry import _DEFAULT_PROMPTS


class TestPromptTemplates:
    """验证所有 prompt 模板的结构完整性。"""

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_task_prompt_exists_in_executor(self, task_type: str):
        """每个 task_type 在 executor 的 _TASK_PROMPTS 中必须有映射。"""
        assert task_type in _TASK_PROMPTS, f"Task type '{task_type}' not in _TASK_PROMPTS"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_task_prompt_exists_in_registry(self, task_type: str):
        """每个 task_type 在 prompt registry 的 _DEFAULT_PROMPTS 中必须有映射。"""
        assert task_type in _DEFAULT_PROMPTS, f"Task type '{task_type}' not in _DEFAULT_PROMPTS"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_prompt_has_message_placeholder(self, task_type: str):
        """每个 prompt 必须包含 {message} 占位符。"""
        prompt = _DEFAULT_PROMPTS[task_type]
        assert "{message}" in prompt, f"Prompt for '{task_type}' missing {{message}}"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_prompt_has_context_block_placeholder(self, task_type: str):
        """每个 prompt 必须包含 {context_block} 占位符。"""
        prompt = _DEFAULT_PROMPTS[task_type]
        assert "{context_block}" in prompt, f"Prompt for '{task_type}' missing {{context_block}}"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_prompt_has_retrieval_block_placeholder(self, task_type: str):
        """每个 prompt 必须包含 {retrieval_block} 或 {retrieval_content} 占位符。"""
        prompt = _DEFAULT_PROMPTS[task_type]
        has_retrieval = "{retrieval_block}" in prompt or "{retrieval_content}" in prompt
        assert has_retrieval, f"Prompt for '{task_type}' missing retrieval placeholder"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_prompt_has_json_output_format(self, task_type: str):
        """每个 prompt 必须包含 JSON 输出格式说明。"""
        prompt = _DEFAULT_PROMPTS[task_type]
        assert "json" in prompt.lower(), f"Prompt for '{task_type}' missing JSON output format"
        assert "summary" in prompt, f"Prompt for '{task_type}' missing 'summary' in output format"
        assert "sections" in prompt, f"Prompt for '{task_type}' missing 'sections' in output format"

    @pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
    def test_prompt_has_strict_rules(self, task_type: str):
        """每个 prompt 必须包含规则/禁止部分。"""
        prompt = _DEFAULT_PROMPTS[task_type]
        has_rules = any(kw in prompt for kw in ["严格规则", "规则", "禁止", "必须"])
        assert has_rules, f"Prompt for '{task_type}' missing rules/constraints section"

    def test_executor_and_registry_prompts_match(self):
        """executor 和 registry 的 prompt 映射必须一致。"""
        assert set(_TASK_PROMPTS.keys()) == set(_DEFAULT_PROMPTS.keys())

    # --- Phase B: 工作流 prompt 特定结构测试 ---

    def test_visit_preparation_required_sections(self):
        """访前准备 prompt 必须包含访前作战卡的所有固定段落。"""
        from sales_agent.prompts.visit_preparation import VISIT_PREPARATION_PROMPT
        required = [
            "本次沟通目标", "客户可能关心的 3 件事", "建议开场话术",
            "必问 3 个问题", "重点价值表达", "本次必须争取的下一步",
            "最大风险提醒", "可直接使用的收口话术",
        ]
        for section in required:
            assert section in VISIT_PREPARATION_PROMPT, \
                f"visit_preparation prompt missing section '{section}'"

    def test_visit_preparation_has_enrichment_sentence(self):
        """访前准备 prompt 必须包含可选补充建议。"""
        from sales_agent.prompts.visit_preparation import VISIT_PREPARATION_PROMPT
        assert "补充客户行业" in VISIT_PREPARATION_PROMPT
        assert "优化一版" in VISIT_PREPARATION_PROMPT

    def test_post_visit_review_required_sections(self):
        """访后复盘 prompt 必须包含访后机会推进卡的所有固定段落。"""
        from sales_agent.prompts.post_visit_review import POST_VISIT_REVIEW_PROMPT
        required = [
            "本次沟通结论", "客户真实需求判断", "当前机会状态",
            "最大卡点", "下一步最佳动作", "可直接发送的跟进话术",
            "是否需要管理者介入", "下次复盘观察点",
        ]
        for section in required:
            assert section in POST_VISIT_REVIEW_PROMPT, \
                f"post_visit_review prompt missing section '{section}'"

    def test_post_visit_review_has_opportunity_classification(self):
        """访后复盘 prompt 必须包含 A/B/C/D 机会分类。"""
        from sales_agent.prompts.post_visit_review import POST_VISIT_REVIEW_PROMPT
        assert "A 可推进" in POST_VISIT_REVIEW_PROMPT or "A可推进" in POST_VISIT_REVIEW_PROMPT
        assert "B 需培育" in POST_VISIT_REVIEW_PROMPT or "B需培育" in POST_VISIT_REVIEW_PROMPT
        assert "C 高风险" in POST_VISIT_REVIEW_PROMPT or "C高风险" in POST_VISIT_REVIEW_PROMPT
        assert "D 暂缓" in POST_VISIT_REVIEW_PROMPT or "D暂缓" in POST_VISIT_REVIEW_PROMPT

    def test_post_visit_review_has_enrichment_sentence(self):
        """访后复盘 prompt 必须包含可选补充建议。"""
        from sales_agent.prompts.post_visit_review import POST_VISIT_REVIEW_PROMPT
        assert "补充客户预算" in POST_VISIT_REVIEW_PROMPT
        assert "重点跟进" in POST_VISIT_REVIEW_PROMPT

    def test_follow_up_planning_required_sections(self):
        """跟进计划 prompt 必须包含所有要求的输出章节。"""
        from sales_agent.prompts.follow_up_planning import FOLLOW_UP_PLANNING_PROMPT
        required = ["跟进时机", "下一步行动", "消息草稿", "备选方案", "风险提醒"]
        for section in required:
            assert section in FOLLOW_UP_PLANNING_PROMPT, \
                f"follow_up_planning prompt missing section '{section}'"

    def test_customer_context_summary_required_sections(self):
        """客户上下文整理 prompt 必须包含所有要求的输出章节。"""
        from sales_agent.prompts.customer_context_summary import CUSTOMER_CONTEXT_SUMMARY_PROMPT
        required = ["客户行业", "核心痛点", "缺失信息", "预算信号", "决策流程"]
        for section in required:
            assert section in CUSTOMER_CONTEXT_SUMMARY_PROMPT, \
                f"customer_context_summary prompt missing section '{section}'"

    def test_deal_advancement_required_sections(self):
        """成交推进 prompt 必须包含所有要求的输出章节。"""
        from sales_agent.prompts.deal_advancement import DEAL_ADVANCEMENT_PROMPT
        required = ["阶段诊断", "成交阻碍", "下一步", "需确认事项", "风险警告"]
        for section in required:
            assert section in DEAL_ADVANCEMENT_PROMPT, \
                f"deal_advancement prompt missing section '{section}'"

    def test_conversation_scoring_required_sections(self):
        """对话评分 prompt 必须包含所有要求的输出章节。"""
        from sales_agent.prompts.conversation_scoring import CONVERSATION_SCORING_PROMPT
        required = ["开场", "需求挖掘", "价值阐述", "异议处理", "收尾", "风险控制", "总分", "改进优先级"]
        for section in required:
            assert section in CONVERSATION_SCORING_PROMPT, \
                f"conversation_scoring prompt missing section '{section}'"

    def test_conversation_scoring_has_rubric(self):
        """对话评分 prompt 必须包含评分维度和 0-100 范围。"""
        from sales_agent.prompts.conversation_scoring import CONVERSATION_SCORING_PROMPT
        assert "0-100" in CONVERSATION_SCORING_PROMPT
        assert "opening" in CONVERSATION_SCORING_PROMPT or "开场" in CONVERSATION_SCORING_PROMPT

    def test_customer_context_no_fabrication_rule(self):
        """客户上下文整理 prompt 必须包含禁止编造规则。"""
        from sales_agent.prompts.customer_context_summary import CUSTOMER_CONTEXT_SUMMARY_PROMPT
        assert "不能推断" in CUSTOMER_CONTEXT_SUMMARY_PROMPT or "不能编造" in CUSTOMER_CONTEXT_SUMMARY_PROMPT
        assert "未知" in CUSTOMER_CONTEXT_SUMMARY_PROMPT
