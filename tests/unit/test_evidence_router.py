"""Evidence Router 单元测试。"""

from __future__ import annotations

import pytest

from sales_agent.services.evidence_router import (
    apply_evidence_policy_guard,
    route_intent_evidence,
)
from sales_agent.services.structured_router_output import EvidenceDecision


class _FakeChatModel:
    """模拟 ChatModel，返回预设响应。"""

    def __init__(self, response: str):
        self._response = response
        self.received_messages: list[dict] | None = None

    async def generate(self, messages, **kwargs):
        self.received_messages = messages
        return self._response


def _make_decision(
    *,
    intent: str = "general_sales_coaching",
    response_mode: str = "direct",
    knowledge_policy: str = "none",
    knowledge_scope: list[str] | None = None,
    retrieval_query: str | None = None,
    confidence: float = 0.95,
    reason_code: str = "test",
) -> EvidenceDecision:
    return EvidenceDecision(
        intent=intent,
        response_mode=response_mode,
        knowledge_policy=knowledge_policy,
        knowledge_scope=knowledge_scope or [],
        retrieval_query=retrieval_query,
        confidence=confidence,
        reason_code=reason_code,
    )


class TestRouteIntentEvidence:
    """route_intent_evidence 集成测试（使用 fake model）。"""

    @pytest.mark.asyncio
    async def test_general_coaching_direct(self):
        """你好 -> general_sales_coaching / none / direct"""
        decision = await route_intent_evidence(
            standalone_query="你好",
            chat_model=_FakeChatModel(
                '{"intent":"general_sales_coaching","response_mode":"direct",'
                '"knowledge_policy":"none","knowledge_scope":[],'
                '"retrieval_query":null,"confidence":0.95,"reason_code":"greeting"}'
            ),
        )
        assert decision.intent == "general_sales_coaching"
        assert decision.knowledge_policy == "none"
        assert decision.response_mode == "direct"

    @pytest.mark.asyncio
    async def test_emotional_support_direct(self):
        """我有点焦虑 -> emotional_support / none / direct"""
        decision = await route_intent_evidence(
            standalone_query="我有点焦虑",
            chat_model=_FakeChatModel(
                '{"intent":"emotional_support","response_mode":"direct",'
                '"knowledge_policy":"none","knowledge_scope":[],'
                '"retrieval_query":null,"confidence":0.9,"reason_code":"emotional_need"}'
            ),
        )
        assert decision.intent == "emotional_support"
        assert decision.knowledge_policy == "none"
        assert decision.response_mode == "direct"

    @pytest.mark.asyncio
    async def test_knowledge_qa_required(self):
        """福多多有哪些产品 -> knowledge_qa / required / retrieve"""
        decision = await route_intent_evidence(
            standalone_query="福多多有哪些产品",
            chat_model=_FakeChatModel(
                '{"intent":"knowledge_qa","response_mode":"retrieve",'
                '"knowledge_policy":"required","knowledge_scope":["产品"],'
                '"retrieval_query":"福多多的产品列表","confidence":0.95,"reason_code":"product_query"}'
            ),
        )
        assert decision.intent == "knowledge_qa"
        assert decision.knowledge_policy == "required"
        assert decision.response_mode == "retrieve"
        assert decision.retrieval_query is not None

    @pytest.mark.asyncio
    async def test_knowledge_qa_price_required(self):
        """福多多多少钱 -> knowledge_qa / required / retrieve"""
        decision = await route_intent_evidence(
            standalone_query="福多多多少钱",
            chat_model=_FakeChatModel(
                '{"intent":"knowledge_qa","response_mode":"retrieve",'
                '"knowledge_policy":"required","knowledge_scope":["价格"],'
                '"retrieval_query":"福多多价格","confidence":0.95,"reason_code":"price_query"}'
            ),
        )
        assert decision.intent == "knowledge_qa"
        assert decision.knowledge_policy == "required"
        assert decision.response_mode == "retrieve"

    @pytest.mark.asyncio
    async def test_script_generation_direct(self):
        """帮我写一句提醒客户回复的话 -> script_generation / none / direct"""
        decision = await route_intent_evidence(
            standalone_query="帮我写一句提醒客户回复的话",
            chat_model=_FakeChatModel(
                '{"intent":"script_generation","response_mode":"direct",'
                '"knowledge_policy":"none","knowledge_scope":[],'
                '"retrieval_query":null,"confidence":0.9,"reason_code":"script_creation"}'
            ),
        )
        assert decision.intent == "script_generation"
        assert decision.knowledge_policy == "none"
        assert decision.response_mode == "direct"

    @pytest.mark.asyncio
    async def test_script_generation_required(self):
        """结合福多多零风险承诺写话术 -> script_generation / required / retrieve"""
        decision = await route_intent_evidence(
            standalone_query="结合福多多零风险承诺写话术",
            chat_model=_FakeChatModel(
                '{"intent":"script_generation","response_mode":"retrieve",'
                '"knowledge_policy":"required","knowledge_scope":["产品知识"],'
                '"retrieval_query":"福多多零风险承诺话术","confidence":0.9,"reason_code":"script_with_knowledge"}'
            ),
        )
        assert decision.intent == "script_generation"
        assert decision.knowledge_policy == "required"
        assert decision.response_mode == "retrieve"

    @pytest.mark.asyncio
    async def test_follow_up_planning_optional(self):
        """客户一直不回复，我该怎么办 -> follow_up_planning / optional / retrieve"""
        decision = await route_intent_evidence(
            standalone_query="客户一直不回复，我该怎么办",
            chat_model=_FakeChatModel(
                '{"intent":"follow_up_planning","response_mode":"retrieve",'
                '"knowledge_policy":"optional","knowledge_scope":["跟进策略"],'
                '"retrieval_query":"客户不回复跟进策略","confidence":0.9,"reason_code":"follow_up"}'
            ),
        )
        assert decision.intent == "follow_up_planning"
        assert decision.knowledge_policy == "optional"
        assert decision.response_mode == "retrieve"

    # --- 异常处理测试 ---

    @pytest.mark.asyncio
    async def test_invalid_intent_fallback(self):
        """无效 intent 回退到 general_sales_coaching。"""
        decision = await route_intent_evidence(
            standalone_query="测试无效intent",
            chat_model=_FakeChatModel(
                '{"intent":"invalid_type_xyz","response_mode":"direct",'
                '"knowledge_policy":"none","knowledge_scope":[],'
                '"retrieval_query":null,"confidence":0.5,"reason_code":"unknown"}'
            ),
        )
        assert decision.intent == "general_sales_coaching"
        assert decision.knowledge_policy == "none"

    @pytest.mark.asyncio
    async def test_invalid_json_retry_and_fallback(self):
        """两次无效 JSON 回退到确定性兜底。"""
        decision = await route_intent_evidence(
            standalone_query="我的产品有什么功能",
            chat_model=_FakeChatModel("not valid json at all"),
        )
        # 包含"产品"事实信号，应回退到 required
        assert decision.knowledge_policy == "required"
        assert decision.response_mode == "retrieve"
        assert decision.retrieval_query == "我的产品有什么功能"

    @pytest.mark.asyncio
    async def test_non_fact_query_fallback(self):
        """无事实信号时兜底为 none/direct。"""
        decision = await route_intent_evidence(
            standalone_query="今天天气怎么样",
            chat_model=_FakeChatModel("completely invalid"),
        )
        assert decision.knowledge_policy == "none"
        assert decision.response_mode == "direct"

    # --- 策略守卫测试 ---

    def test_policy_guard_upgrades_product_fact(self):
        """产品事实信号升级 none -> required。"""
        decision = _make_decision(knowledge_policy="none", response_mode="direct")
        result = apply_evidence_policy_guard("我们产品有什么功能", decision)
        assert result.knowledge_policy == "required"
        assert result.response_mode == "retrieve"
        assert result.reason_code == "policy_guard_upgraded_to_required"

    def test_policy_guard_upgrades_price_fact(self):
        """价格事实信号升级 none -> required。"""
        decision = _make_decision(knowledge_policy="none", response_mode="direct")
        result = apply_evidence_policy_guard("这个多少钱", decision)
        assert result.knowledge_policy == "required"
        assert result.response_mode == "retrieve"

    def test_policy_guard_does_not_upgrade_already_required(self):
        """已经是 required 的不重复升级。"""
        decision = _make_decision(
            knowledge_policy="required", response_mode="retrieve",
            retrieval_query="test query",
        )
        result = apply_evidence_policy_guard("我们产品有什么功能", decision)
        # 维持 required
        assert result.knowledge_policy == "required"
        assert result.response_mode == "retrieve"

    def test_policy_guard_sets_retrieval_query_when_missing(self):
        """事实信号升级并填充 retrieval_query。"""
        # 从 optional 开始，无 retrieval_query
        decision = _make_decision(
            knowledge_policy="optional", response_mode="retrieve",
            retrieval_query=None,
        )
        result = apply_evidence_policy_guard("福多多有哪些产品", decision)
        # 事实信号升级为 required，retrieval_query 使用原始查询
        assert result.knowledge_policy == "required"
        assert result.retrieval_query == "福多多有哪些产品"

    def test_policy_guard_greeting_downgrade(self):
        """问候语降级 optional -> none。"""
        decision = _make_decision(
            knowledge_policy="optional", response_mode="retrieve"
        )
        result = apply_evidence_policy_guard("你好", decision)
        assert result.knowledge_policy == "none"
        assert result.response_mode == "direct"
        assert result.reason_code == "policy_guard_downgraded_to_none"

    def test_policy_guard_does_not_downgrade_required_for_greeting(self):
        """问候语不降级已 required 的策略。"""
        decision = _make_decision(
            knowledge_policy="required", response_mode="retrieve",
            retrieval_query="test query",
        )
        result = apply_evidence_policy_guard("你好", decision)
        # 问候语信号不降级 required
        assert result.knowledge_policy == "required"
        assert result.response_mode == "retrieve"

    def test_policy_guard_fact_wins_over_greeting(self):
        """事实信号优先于问候信号。"""
        decision = _make_decision(knowledge_policy="none", response_mode="direct")
        result = apply_evidence_policy_guard("你好，产品多少钱", decision)
        # 包含"产品"和"多少钱"事实信号
        assert result.knowledge_policy == "required"
        assert result.response_mode == "retrieve"

    def test_policy_guard_emotional_downgrade(self):
        """情绪信号降级 optional -> none。"""
        decision = _make_decision(
            knowledge_policy="optional", response_mode="retrieve"
        )
        result = apply_evidence_policy_guard("我很焦虑", decision)
        assert result.knowledge_policy == "none"
        assert result.response_mode == "direct"
