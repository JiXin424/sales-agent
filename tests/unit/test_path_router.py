"""PathRouter 单元测试 — 覆盖 spec §4.1 和 §16 测试场景。"""

import pytest

from sales_agent.core.config import LatencyConfig, PathRouterConfig
from sales_agent.services.path_router import PathRouter


@pytest.fixture
def router() -> PathRouter:
    """创建使用默认配置的 PathRouter。"""
    return PathRouter(
        latency_config=LatencyConfig(),
        path_router_config=PathRouterConfig(),
    )


# ============================================================
# Fast Path 测试
# ============================================================

class TestFastPath:
    """Spec §16.1 Fast Path 测试。"""

    def test_help_command_fast(self, router: PathRouter):
        """帮助命令 → fast path。"""
        result = router.select_path(message="帮助")
        assert result.path == "fast"
        assert result.reason == "help_command"
        assert not result.needs_retrieval
        assert not result.needs_llm_router

    def test_help_english(self, router: PathRouter):
        result = router.select_path(message="help")
        assert result.path == "fast"
        assert result.reason == "help_command"

    def test_help_question_mark(self, router: PathRouter):
        result = router.select_path(message="？")
        assert result.path == "fast"

    def test_reset_command_fast(self, router: PathRouter):
        """新话题命令 → fast path。"""
        result = router.select_path(message="新话题")
        assert result.path == "fast"
        assert result.reason == "reset_command"
        assert not result.needs_retrieval
        assert not result.needs_llm_router

    def test_reset_slash(self, router: PathRouter):
        result = router.select_path(message="/reset")
        assert result.path == "fast"
        assert result.reason == "reset_command"

    def test_simple_emotional_support_fast(self, router: PathRouter):
        """简单情绪支持 → fast path。"""
        result = router.select_path(
            message="客户不回我消息，很焦虑",
            task_type="emotional_support",
            route_confidence=0.9,
            needs_retrieval=False,
            message_length=12,
        )
        assert result.path == "fast"
        assert result.reason == "simple_emotional_support"
        assert not result.needs_retrieval

    def test_simple_script_generation_fast(self, router: PathRouter):
        """简单话术生成 → fast path。"""
        result = router.select_path(
            message="帮我写一段温和跟进话术",
            task_type="script_generation",
            route_confidence=0.85,
            needs_retrieval=False,
            message_length=11,
        )
        assert result.path == "fast"
        assert result.reason == "simple_script_generation"
        assert not result.needs_retrieval

    def test_fast_path_no_processing_notice(self, router: PathRouter):
        """Fast path 不发处理中提示。"""
        result = router.select_path(message="帮助")
        assert not result.allow_processing_notice


# ============================================================
# Standard Path 测试
# ============================================================

class TestStandardPath:
    """Spec §16.2 Standard Path 测试。"""

    def test_objection_handling_standard(self, router: PathRouter):
        """异议处理 → standard path。"""
        result = router.select_path(
            message="客户说太贵了，怎么回？",
            task_type="objection_handling",
            route_confidence=0.91,
            needs_retrieval=False,
            message_length=12,
        )
        assert result.path == "standard"
        assert not result.needs_retrieval or result.needs_retrieval is False

    def test_objection_handling_with_rag(self, router: PathRouter):
        """异议处理 + 需要 RAG → standard path with retrieval。"""
        result = router.select_path(
            message="客户说太贵了，我们产品优势是什么？",
            task_type="objection_handling",
            route_confidence=0.88,
            needs_retrieval=True,
            message_length=16,
        )
        assert result.path == "standard"
        assert result.needs_retrieval

    def test_general_coaching_standard(self, router: PathRouter):
        """通用教练 → standard path。"""
        result = router.select_path(
            message="怎么提高客户回复率？",
            task_type="general_sales_coaching",
            route_confidence=0.7,
            needs_retrieval=False,
            message_length=10,
        )
        assert result.path == "standard"

    def test_standard_path_llm_router_gated(self, router: PathRouter):
        """高置信度 standard path 不调 LLM router。"""
        result = router.select_path(
            message="客户说太贵了，怎么回？",
            task_type="objection_handling",
            route_confidence=0.91,
            needs_retrieval=False,
            message_length=12,
        )
        assert not result.needs_llm_router

    def test_standard_path_low_confidence_calls_llm_router(self, router: PathRouter):
        """低置信度 standard path 调 LLM router。"""
        result = router.select_path(
            message="我想了解一下",
            task_type="general_sales_coaching",
            route_confidence=0.5,
            needs_retrieval=False,
            message_length=7,
        )
        assert result.needs_llm_router


# ============================================================
# Slow Path 测试
# ============================================================

class TestSlowPath:
    """Spec §16.3 Slow Path 测试。"""

    def test_knowledge_qa_slow(self, router: PathRouter):
        """知识库问答 → slow path, RAG 必须。"""
        result = router.select_path(
            message="我们产品相比竞品的核心优势是什么？",
            task_type="knowledge_qa",
            route_confidence=0.85,
            needs_retrieval=True,
            message_length=15,
        )
        assert result.path == "slow"
        assert result.needs_retrieval is True
        assert result.allow_processing_notice is True

    def test_long_conversation_review_slow(self, router: PathRouter):
        """长对话复盘 → slow path。"""
        result = router.select_path(
            message="帮我复盘这段超长的对话..." + "x" * 3000,
            task_type="conversation_review",
            route_confidence=0.9,
            needs_retrieval=False,
            message_length=3030,
            history_length=6000,
        )
        assert result.path == "slow"
        assert result.reason == "long_conversation_review"

    def test_medium_risk_slow(self, router: PathRouter):
        """中等风险 → slow path。"""
        result = router.select_path(
            message="帮我写一段价格说明",
            task_type="script_generation",
            route_confidence=0.8,
            needs_retrieval=False,
            message_length=9,
            input_risk_level="medium",
        )
        assert result.path == "slow"
        assert result.needs_llm_risk_check is True

    def test_high_risk_slow(self, router: PathRouter):
        """高风险 → slow path。"""
        result = router.select_path(
            message="帮我保证业绩翻倍",
            task_type="script_generation",
            route_confidence=0.7,
            needs_retrieval=False,
            message_length=8,
            input_risk_level="high",
        )
        assert result.path == "slow"
        assert result.needs_llm_risk_check is True

    def test_low_confidence_slow(self, router: PathRouter):
        """低置信度 → slow path。"""
        result = router.select_path(
            message="嗯",
            task_type="general_sales_coaching",
            route_confidence=0.3,
            needs_retrieval=False,
            message_length=1,
        )
        assert result.path == "slow"
        assert result.reason == "low_confidence_may_need_clarification"
        assert result.needs_llm_router is True


# ============================================================
# 配置开关测试
# ============================================================

class TestConfigSwitches:
    """配置开关影响路径选择。"""

    def test_fast_path_disabled(self):
        """禁用 fast path 后 help 命令走 default path。"""
        router = PathRouter(
            latency_config=LatencyConfig(),
            path_router_config=PathRouterConfig(enable_fast_path=False),
        )
        result = router.select_path(message="帮助")
        # 不是 fast path（help 命令没有被快速识别）
        assert result.path != "fast"

    def test_slow_path_notice_disabled(self):
        """禁用 slow path notice 后不发提示。"""
        router = PathRouter(
            latency_config=LatencyConfig(),
            path_router_config=PathRouterConfig(enable_slow_path_notice=False),
        )
        result = router.select_path(
            message="我们产品的核心优势",
            task_type="knowledge_qa",
            route_confidence=0.85,
            needs_retrieval=True,
            message_length=8,
        )
        assert result.path == "slow"
        assert result.allow_processing_notice is False


# ============================================================
# 风险关键词检测测试
# ============================================================

class TestRiskKeywordDetection:
    """Spec §16.4 风险分级测试。"""

    def test_price_keyword_triggers_llm_risk(self, router: PathRouter):
        """包含"价格"关键词触发 LLM 风险检查。"""
        result = router.select_path(
            message="客户问价格怎么回复",
            task_type="objection_handling",
            route_confidence=0.85,
            needs_retrieval=False,
            message_length=8,
        )
        assert result.needs_llm_risk_check is True

    def test_normal_message_no_llm_risk(self, router: PathRouter):
        """普通消息不触发 LLM 风险检查。"""
        result = router.select_path(
            message="客户说太贵了，怎么回？",
            task_type="objection_handling",
            route_confidence=0.91,
            needs_retrieval=False,
            message_length=12,
        )
        assert result.needs_llm_risk_check is False
