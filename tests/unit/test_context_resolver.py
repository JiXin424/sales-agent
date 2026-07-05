"""单元测试：Context Resolver — 话语和话题关系解析。

所有测试使用 StubModel（不调用真实 LLM），只验证 resolve_context 的行为。
"""

from __future__ import annotations

import json

import pytest

from sales_agent.services.context_resolver import resolve_context
from sales_agent.services.structured_router_output import ContextDecision


# ============================================================
# Stub models for testing (no real LLM calls)
# ============================================================


class StubModel:
    """Returns a fixed JSON dict on every call."""

    def __init__(self, response_data: dict) -> None:
        self.response_data = response_data
        self.call_count = 0

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        self.call_count += 1
        return json.dumps(self.response_data, ensure_ascii=False)


class StubModelFailsOnce:
    """Returns invalid JSON on the first call, then valid JSON."""

    def __init__(self, valid_data: dict) -> None:
        self.valid_data = valid_data
        self.call_count = 0

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        self.call_count += 1
        if self.call_count == 1:
            return "not valid json {{{"
        return json.dumps(self.valid_data, ensure_ascii=False)


class StubModelAlwaysFails:
    """Returns invalid JSON on every call."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        self.call_count += 1
        return "garbage {{{ broken [[["


# ============================================================
# Fake topic for tests that need a topic object
# ============================================================

class FakeTopic:
    """Minimal stand-in for ConversationTopic used in deterministic fallback tests."""

    def __init__(self, summary: str = "", current_goal: str = "") -> None:
        self.summary = summary
        self.current_goal = current_goal


# ============================================================
# within-turn correction tests
# ============================================================

class TestWithinTurnRevision:
    """within-turn correction: user changes their mind mid-message."""

    @pytest.mark.asyncio
    async def test_within_turn_revision_retains_entity(self) -> None:
        """User revises request but retains entity reference (exact brief example)."""
        model = StubModel({
            "turn_relation": "new",
            "standalone_query": "查询福多多的主要竞品及对比资料",
            "retained_entities": ["福多多"],
            "retracted_goals": ["查询福多多产品"],
            "missing_references": [],
            "confidence": 0.95,
            "reason_code": "within_turn_correction",
        })
        result = await resolve_context(
            message="帮我找一下福多多的产品，算了，还是找一下竞品吧。",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.standalone_query == "查询福多多的主要竞品及对比资料"
        assert result.retained_entities == ["福多多"]
        assert result.retracted_goals == ["查询福多多产品"]
        assert result.turn_relation == "new"
        assert result.reason_code == "within_turn_correction"


# ============================================================
# Relation classification tests
# ============================================================

class TestRelationClassification:
    """Different turn_relation classifications returned by the model."""

    @pytest.mark.asyncio
    async def test_pronoun_continuation(self) -> None:
        """Pronoun reference continues existing topic."""
        model = StubModel({
            "turn_relation": "continue",
            "standalone_query": "查询福多多产品的价格",
            "retained_entities": ["福多多"],
            "retracted_goals": [],
            "missing_references": [],
            "confidence": 0.92,
            "reason_code": "pronoun_reference",
        })
        result = await resolve_context(
            message="它的价格是多少？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "continue"
        assert "福多多" in result.retained_entities
        assert result.reason_code == "pronoun_reference"

    @pytest.mark.asyncio
    async def test_explicit_revision(self) -> None:
        """User explicitly revises their previous request."""
        model = StubModel({
            "turn_relation": "revise",
            "standalone_query": "查询福多多的竞品分析",
            "retained_entities": ["福多多"],
            "retracted_goals": ["查询福多多产品详情"],
            "missing_references": [],
            "confidence": 0.88,
            "reason_code": "goal_revision",
        })
        result = await resolve_context(
            message="算了，还是查一下竞品吧。",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "revise"
        assert result.retracted_goals == ["查询福多多产品详情"]
        assert result.retained_entities == ["福多多"]

    @pytest.mark.asyncio
    async def test_unrelated_new_topic(self) -> None:
        """User starts an unrelated new topic."""
        model = StubModel({
            "turn_relation": "new",
            "standalone_query": "今天的天气情况",
            "retained_entities": [],
            "retracted_goals": [],
            "missing_references": [],
            "confidence": 0.98,
            "reason_code": "new_topic",
        })
        result = await resolve_context(
            message="今天天气怎么样？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "new"
        assert result.standalone_query == "今天的天气情况"
        assert result.reason_code == "new_topic"

    @pytest.mark.asyncio
    async def test_ambiguous_missing_reference(self) -> None:
        """Ambiguous reference without enough context."""
        model = StubModel({
            "turn_relation": "ambiguous",
            "standalone_query": "",
            "retained_entities": [],
            "retracted_goals": [],
            "missing_references": ["那个项目"],
            "confidence": 0.45,
            "reason_code": "missing_reference",
        })
        result = await resolve_context(
            message="那个项目怎么样了？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "ambiguous"
        assert "那个项目" in result.missing_references
        assert result.reason_code == "missing_reference"


# ============================================================
# Retry and deterministic fallback tests
# ============================================================

class TestRetryAndFallback:
    """Retry on parse failure and deterministic fallback."""

    @pytest.mark.asyncio
    async def test_malformed_output_retry_once(self) -> None:
        """Retries once when model returns malformed JSON."""
        model = StubModelFailsOnce({
            "turn_relation": "continue",
            "standalone_query": "查询福多多产品的价格",
            "retained_entities": ["福多多"],
            "retracted_goals": [],
            "missing_references": [],
            "confidence": 0.9,
            "reason_code": "continuation",
        })
        result = await resolve_context(
            message="它的价格是多少？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.standalone_query == "查询福多多产品的价格"
        assert model.call_count == 2  # First failed, second succeeded
        assert result.turn_relation == "continue"

    @pytest.mark.asyncio
    async def test_two_failures_return_ambiguous(self) -> None:
        """Two consecutive failures return deterministic ambiguous."""
        model = StubModelAlwaysFails()
        result = await resolve_context(
            message="这个怎么卖？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "ambiguous"
        assert result.reason_code == "resolver_failure"
        assert result.confidence == 0.5
        assert model.call_count == 2

    @pytest.mark.asyncio
    async def test_continuation_marker_with_topic(self) -> None:
        """Continuation markers produce 'continue' when a topic exists."""
        model = StubModelAlwaysFails()
        topic = FakeTopic(summary="客户询问福多多产品价格", current_goal="查询福多多产品价格")
        result = await resolve_context(
            message="继续说下去",
            topic=topic,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "continue"
        assert result.reason_code == "resolver_failure"

    @pytest.mark.asyncio
    async def test_continuation_marker_without_topic_is_ambiguous(self) -> None:
        """Continuation markers without a topic fall back to ambiguous."""
        model = StubModelAlwaysFails()
        result = await resolve_context(
            message="继续说下去",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "ambiguous"
        assert result.reason_code == "resolver_failure"

    @pytest.mark.asyncio
    async def test_new_topic_marker(self) -> None:
        """New-topic markers produce 'new' decision."""
        model = StubModelAlwaysFails()
        result = await resolve_context(
            message="新话题",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "new"
        assert result.reason_code == "resolver_failure"

    @pytest.mark.asyncio
    async def test_ambiguous_fallback_without_markers(self) -> None:
        """Without topic or markers, returns ambiguous."""
        model = StubModelAlwaysFails()
        result = await resolve_context(
            message="这个怎么卖？",
            topic=None,
            recent_messages=[],
            chat_model=model,
        )
        assert result.turn_relation == "ambiguous"
        assert result.reason_code == "resolver_failure"
        assert result.standalone_query == ""
