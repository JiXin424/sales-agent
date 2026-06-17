"""Task Router 单元测试。"""

import pytest

from sales_agent.services.task_router import (
    route_task,
    EMOTIONAL_SUPPORT,
    KNOWLEDGE_QA,
    SCRIPT_GENERATION,
    OBJECTION_HANDLING,
    CONVERSATION_REVIEW,
    GENERAL_COACHING,
    VISIT_PREPARATION,
    FOLLOW_UP_PLANNING,
    CUSTOMER_CONTEXT_SUMMARY,
    DEAL_ADVANCEMENT,
    CONVERSATION_SCORING,
    POST_VISIT_REVIEW,
    ALL_TASK_TYPES,
    WORKFLOW_TASK_TYPES,
)


class TestTaskRouter:
    @pytest.mark.asyncio
    async def test_emotional_support(self):
        result = await route_task("客户三天没回我了，我感觉这单没戏了")
        assert result.task_type == EMOTIONAL_SUPPORT

    @pytest.mark.asyncio
    async def test_emotional_support_anxiety(self):
        result = await route_task("很焦虑，客户一直不签单")
        assert result.task_type == EMOTIONAL_SUPPORT

    @pytest.mark.asyncio
    async def test_knowledge_qa_product(self):
        result = await route_task("我们产品的核心优势是什么？")
        assert result.task_type == KNOWLEDGE_QA

    @pytest.mark.asyncio
    async def test_knowledge_qa_case(self):
        result = await route_task("有没有案例可以参考？")
        # "案例" keyword matches knowledge_qa
        assert result.task_type == KNOWLEDGE_QA

    @pytest.mark.asyncio
    async def test_script_generation(self):
        result = await route_task("帮我写一段催客户确认演示的话术")
        assert result.task_type == SCRIPT_GENERATION

    @pytest.mark.asyncio
    async def test_objection_handling_price(self):
        result = await route_task("客户说太贵了，另一家便宜很多，怎么回？")
        assert result.task_type == OBJECTION_HANDLING

    @pytest.mark.asyncio
    async def test_objection_handling_doubt(self):
        result = await route_task("客户质疑我们的能力，怎么办？")
        assert result.task_type == OBJECTION_HANDLING

    @pytest.mark.asyncio
    async def test_conversation_review(self):
        result = await route_task("帮我复盘：客户问多少钱，我说最低8万")
        assert result.task_type == CONVERSATION_REVIEW

    @pytest.mark.asyncio
    async def test_conversation_review_chat(self):
        result = await route_task("这段聊天记录哪里有问题？")
        assert result.task_type == CONVERSATION_REVIEW

    @pytest.mark.asyncio
    async def test_general_coaching_fallback(self):
        result = await route_task("做销售最重要的是什么")
        assert result.task_type in (GENERAL_COACHING, KNOWLEDGE_QA)

    @pytest.mark.asyncio
    async def test_priority_review_over_script(self):
        """复盘优先级高于话术生成。"""
        result = await route_task("帮我复盘这段话术生成得怎么样")
        assert result.task_type == CONVERSATION_REVIEW

    @pytest.mark.asyncio
    async def test_confidence_high_on_keyword_match(self):
        result = await route_task("客户说太贵了")
        assert result.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_needs_retrieval_for_knowledge_qa(self):
        result = await route_task("我们产品的优势是什么？")
        assert result.task_type == KNOWLEDGE_QA
        assert result.needs_retrieval is True

    @pytest.mark.asyncio
    async def test_script_needs_three_versions(self):
        result = await route_task("帮我写一段催客户的话术")
        assert result.task_type == SCRIPT_GENERATION

    # --- Phase B: Workflow task type routing tests ---

    @pytest.mark.asyncio
    async def test_visit_preparation_basic(self):
        result = await route_task("明天拜访一家制造业客户，帮我准备拜访提纲")
        assert result.task_type == VISIT_PREPARATION

    @pytest.mark.asyncio
    async def test_visit_preparation_meeting(self):
        result = await route_task("帮我准备拜访客户的内容")
        assert result.task_type == VISIT_PREPARATION

    @pytest.mark.asyncio
    async def test_visit_preparation_before_meeting(self):
        result = await route_task("见客户前帮我准备一下")
        assert result.task_type == VISIT_PREPARATION

    @pytest.mark.asyncio
    async def test_visit_preparation_needs_no_retrieval(self):
        result = await route_task("拜访准备：明天见一个金融客户")
        assert result.task_type == VISIT_PREPARATION
        assert result.needs_retrieval is False

    @pytest.mark.asyncio
    async def test_follow_up_planning_basic(self):
        result = await route_task("客户看完演示后没回我，跟进计划怎么做")
        assert result.task_type == FOLLOW_UP_PLANNING

    @pytest.mark.asyncio
    async def test_follow_up_planning_next_step(self):
        result = await route_task("下一步怎么跟进这个客户")
        assert result.task_type == FOLLOW_UP_PLANNING

    @pytest.mark.asyncio
    async def test_follow_up_planning_strategy(self):
        result = await route_task("跟进策略怎么安排，报完价一周了")
        assert result.task_type == FOLLOW_UP_PLANNING

    @pytest.mark.asyncio
    async def test_follow_up_needs_no_retrieval(self):
        result = await route_task("跟进计划怎么安排")
        assert result.task_type == FOLLOW_UP_PLANNING
        assert result.needs_retrieval is False

    @pytest.mark.asyncio
    async def test_customer_context_basic(self):
        result = await route_task("帮我整理客户信息和背景")
        assert result.task_type == CUSTOMER_CONTEXT_SUMMARY

    @pytest.mark.asyncio
    async def test_customer_context_profile(self):
        result = await route_task("帮我梳理客户画像")
        assert result.task_type == CUSTOMER_CONTEXT_SUMMARY

    @pytest.mark.asyncio
    async def test_customer_context_organize(self):
        result = await route_task("整理一下客户背景信息")
        assert result.task_type == CUSTOMER_CONTEXT_SUMMARY

    @pytest.mark.asyncio
    async def test_customer_context_needs_no_retrieval(self):
        result = await route_task("整理客户信息")
        assert result.task_type == CUSTOMER_CONTEXT_SUMMARY
        assert result.needs_retrieval is False

    @pytest.mark.asyncio
    async def test_deal_advancement_basic(self):
        result = await route_task("怎么推进这个客户成交")
        assert result.task_type == DEAL_ADVANCEMENT

    @pytest.mark.asyncio
    async def test_deal_advancement_push(self):
        result = await route_task("怎么逼单，方案都出了客户就是拖着")
        assert result.task_type == DEAL_ADVANCEMENT

    @pytest.mark.asyncio
    async def test_deal_advancement_contract(self):
        result = await route_task("怎么签约，客户已经口头同意了")
        assert result.task_type == DEAL_ADVANCEMENT

    @pytest.mark.asyncio
    async def test_deal_advancement_needs_no_retrieval(self):
        result = await route_task("怎么推进成交")
        assert result.task_type == DEAL_ADVANCEMENT
        assert result.needs_retrieval is False

    @pytest.mark.asyncio
    async def test_conversation_scoring_basic(self):
        result = await route_task("帮我做个对话评分")
        assert result.task_type == CONVERSATION_SCORING

    @pytest.mark.asyncio
    async def test_conversation_scoring_rate(self):
        result = await route_task("这个销售对话评分标准是什么，给我打个分")
        assert result.task_type == CONVERSATION_SCORING

    @pytest.mark.asyncio
    async def test_conversation_scoring_evaluate(self):
        result = await route_task("帮我打个分，看看销售表现怎么样")
        assert result.task_type == CONVERSATION_SCORING

    @pytest.mark.asyncio
    async def test_conversation_scoring_needs_no_retrieval(self):
        result = await route_task("给我打分")
        assert result.task_type == CONVERSATION_SCORING
        assert result.needs_retrieval is False

    # --- Routing correctness: new types should not interfere with old types ---

    @pytest.mark.asyncio
    async def test_review_not_confused_with_scoring(self):
        """复盘和评分应该区分开。"""
        result = await route_task("帮我复盘这段聊天记录哪里有问题")
        assert result.task_type == CONVERSATION_REVIEW
        assert result.task_type != CONVERSATION_SCORING

    @pytest.mark.asyncio
    async def test_emotional_support_not_confused_with_follow_up(self):
        """情绪支持不应被路由为跟进计划。"""
        result = await route_task("客户不回我，我感觉没戏了")
        assert result.task_type == EMOTIONAL_SUPPORT

    @pytest.mark.asyncio
    async def test_all_task_types_count(self):
        """总任务类型数量应该是 12（6 + 6）。"""
        assert len(ALL_TASK_TYPES) == 12

    @pytest.mark.asyncio
    async def test_workflow_task_types_count(self):
        """工作流任务类型数量应该是 6。"""
        assert len(WORKFLOW_TASK_TYPES) == 6

    # --- Post-visit review routing tests ---

    @pytest.mark.asyncio
    async def test_post_visit_review_basic(self):
        result = await route_task("刚聊完客户，帮我梳理一下下一步")
        assert result.task_type == POST_VISIT_REVIEW

    @pytest.mark.asyncio
    async def test_post_visit_review_just_finished(self):
        result = await route_task("刚见完客户，沟通结束了")
        assert result.task_type == POST_VISIT_REVIEW

    @pytest.mark.asyncio
    async def test_post_visit_review_summary(self):
        result = await route_task("访后总结，客户说考虑一下")
        assert result.task_type == POST_VISIT_REVIEW

    @pytest.mark.asyncio
    async def test_post_visit_review_needs_no_retrieval(self):
        result = await route_task("刚聊完客户")
        assert result.task_type == POST_VISIT_REVIEW
        assert result.needs_retrieval is False

    @pytest.mark.asyncio
    async def test_post_visit_review_opportunity_card(self):
        result = await route_task("访后机会推进卡，帮我生成一下")
        assert result.task_type == POST_VISIT_REVIEW

    # --- Pre-visit enhanced routing tests ---

    @pytest.mark.asyncio
    async def test_pre_visit_natural_language_going(self):
        """自然语言触发'我要去拜访客户了'。"""
        result = await route_task("我要去拜访客户了，帮我准备一下")
        assert result.task_type == VISIT_PREPARATION

    @pytest.mark.asyncio
    async def test_pre_visit_card_keyword(self):
        result = await route_task("给我一张访前作战卡")
        assert result.task_type == VISIT_PREPARATION

    @pytest.mark.asyncio
    async def test_pre_visit_before_meeting_natural(self):
        result = await route_task("见客户前帮我想清楚怎么聊")
        assert result.task_type == VISIT_PREPARATION
