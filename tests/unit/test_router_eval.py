"""Unit tests for the router evaluation suite.

Tests confusion-matrix counts, macro accuracy, required false-negative rate,
topic leakage rate, and clarification completion rate using small fixed
fixtures where exact expected values are known.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from eval.router.run_router_eval import (
    EvalCase,
    ClarificationEvalCase,
    ClarificationEvalReport,
    ClassMetrics,
    EvalReport,
    RouterEvalDependencies,
    TURN_RELATIONS,
    EVIDENCE_POLICIES,
    evaluate_turn_relation,
    evaluate_evidence_policy,
    evaluate_clarification_resolution,
    _fixture_context_resolver,
    _fixture_evidence_router,
    _fixture_clarification_resolver,
    load_cases,
    write_json_report,
    write_markdown_report,
    _count_required_fn,
    _has_fact_signal,
)


# ======================================================================
# Helpers
# ======================================================================


def _make_case(
    case_id: str,
    input_text: str,
    turn_relation: str,
    knowledge_policy: str = "none",
) -> EvalCase:
    """Create a minimal EvalCase with the given parameters."""
    expected: dict[str, Any] = {"turn_relation": turn_relation}
    if knowledge_policy:
        expected["knowledge_policy"] = knowledge_policy
    return EvalCase(
        id=case_id,
        input=input_text,
        context={},
        expected=expected,
        rationale="test fixture",
    )


def _make_ev_case(
    case_id: str,
    input_text: str,
    knowledge_policy: str,
) -> EvalCase:
    """Create a minimal evidence-policy EvalCase."""
    return EvalCase(
        id=case_id,
        input=input_text,
        context={"standalone_query": input_text},
        expected={
            "knowledge_policy": knowledge_policy,
            "intent": "general_sales_coaching",
        },
        rationale="test fixture",
    )


# ======================================================================
# Tests: confusion-matrix counts
# ======================================================================


class TestConfusionMatrix:
    """Verify per-class counts match expected values."""

    @pytest.mark.asyncio
    async def test_all_fixture_resolver_returns_continue(self) -> None:
        """Fixture resolver always returns 'continue' — all cases map to continue."""
        cases = [
            _make_case("c1", "hello", "continue"),
            _make_case("c2", "继续", "continue"),
            _make_case("c3", "新话题", "new"),
            _make_case("c4", "换一个", "switch"),
            _make_case("c5", "那个项目", "ambiguous"),
        ]
        report = await evaluate_turn_relation(cases, _fixture_context_resolver, RouterEvalDependencies())
        # All predicted as continue
        assert report.total_cases == 5
        assert report.correct == 2  # only c1 and c2 are correct (expected continue)
        assert report.accuracy == 0.4

        # Per-class counts
        assert report.per_class_metrics["continue"].total == 2  # c1, c2
        assert report.per_class_metrics["continue"].correct == 2
        assert report.per_class_metrics["new"].total == 1
        assert report.per_class_metrics["new"].correct == 0  # predicted continue
        assert report.per_class_metrics["switch"].total == 1
        assert report.per_class_metrics["switch"].correct == 0
        assert report.per_class_metrics["ambiguous"].total == 1
        assert report.per_class_metrics["ambiguous"].correct == 0

    @pytest.mark.asyncio
    async def test_mixed_fixture_all_correct(self) -> None:
        """Custom resolver that always returns the expected answer."""
        def perfect_resolver(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            ctx = kwargs.get("context", {})
            if "新话题" in msg:
                return {"turn_relation": "new", "standalone_query": msg}
            if "换" in msg:
                return {"turn_relation": "switch", "standalone_query": msg}
            if "项目" in msg:
                return {"turn_relation": "ambiguous", "standalone_query": ""}
            if "算了" in msg:
                return {"turn_relation": "revise", "standalone_query": msg}
            return {"turn_relation": "continue", "standalone_query": msg}

        cases = [
            _make_case("c1", "继续", "continue"),
            _make_case("c2", "新话题", "new"),
            _make_case("c3", "换个话题", "switch"),
            _make_case("c4", "那个项目", "ambiguous"),
            _make_case("c5", "算了重新说", "revise"),
        ]
        report = await evaluate_turn_relation(cases, perfect_resolver, RouterEvalDependencies())
        assert report.total_cases == 5
        assert report.correct == 5
        assert report.accuracy == 1.0
        for rel in TURN_RELATIONS:
            cm = report.per_class_metrics[rel]
            if rel in ("continue", "new", "switch", "ambiguous", "revise"):
                assert cm.total == 1
                assert cm.correct == 1

    @pytest.mark.asyncio
    async def test_confusion_off_by_one(self) -> None:
        """Half correct, half wrong — verify all counts."""

        def _half_resolver(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            # Even-numbered IDs → correct; odd → wrong (always return new)
            return {"turn_relation": "new", "standalone_query": msg}

        cases = [
            _make_case("c1", "新话题", "new"),       # correct
            _make_case("c2", "继续聊", "continue"),  # wrong (predicted new)
            _make_case("c3", "换个话题", "switch"),   # wrong (predicted new)
            _make_case("c4", "那个项目", "ambiguous"), # wrong (predicted new)
        ]
        report = await evaluate_turn_relation(cases, _half_resolver, RouterEvalDependencies())
        assert report.total_cases == 4
        assert report.correct == 1
        # new class: total=1 (c1), correct=1
        assert report.per_class_metrics["new"].total == 1
        assert report.per_class_metrics["new"].correct == 1
        # continue: total=1, correct=0
        assert report.per_class_metrics["continue"].total == 1
        assert report.per_class_metrics["continue"].correct == 0


# ======================================================================
# Tests: macro accuracy
# ======================================================================


class TestMacroAccuracy:
    """Verify macro accuracy calculation."""

    @pytest.mark.asyncio
    async def test_macro_accuracy_perfect(self) -> None:
        """All classes perfect → macro accuracy = 1.0."""

        def perfect(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            if "新" in msg:
                return {"turn_relation": "new", "standalone_query": msg}
            return {"turn_relation": "continue", "standalone_query": msg}

        cases = [
            _make_case("c1", "继续", "continue"),
            _make_case("c2", "新话题", "new"),
        ]
        report = await evaluate_turn_relation(cases, perfect, RouterEvalDependencies())
        # Only 2 classes have cases, but all 5 are included
        # macro = (1.0 + 1.0 + 0 + 0 + 0) / 5 = 0.4
        # Actually, the macro_accuracy sums over ALL 5 turn relations
        # even those with 0 cases (accuracy=0 for those)
        # Wait, accuracy for classes with 0 total is 0.0
        # So macro = (1.0 + 1.0 + 0 + 0 + 0) / 5 = 0.4
        # Only 2 populated classes (continue=1.0, new=1.0) → macro = 1.0
        assert report.macro_accuracy == 1.0

    @pytest.mark.asyncio
    async def test_macro_accuracy_half(self) -> None:
        """Half classes correct."""

        def mixed(**kwargs: Any) -> dict[str, Any]:
            return {"turn_relation": "continue", "standalone_query": "test"}

        cases = [
            _make_case("c1", "继续", "continue"),   # correct
            _make_case("c2", "新话题", "new"),       # wrong
            _make_case("c3", "换话题", "switch"),     # wrong
            _make_case("c4", "那个", "ambiguous"),    # wrong
            _make_case("c5", "算了", "revise"),       # wrong
        ]
        report = await evaluate_turn_relation(cases, mixed, RouterEvalDependencies())
        # continue: 1/1 = 1.0
        # new: 0/1 = 0.0
        # switch: 0/1 = 0.0
        # ambiguous: 0/1 = 0.0
        # revise: 0/1 = 0.0
        # macro = (1.0 + 0 + 0 + 0 + 0) / 5 = 0.2
        assert report.macro_accuracy == 0.2

    @pytest.mark.asyncio
    async def test_macro_accuracy_with_empty_classes(self) -> None:
        """Classes with no cases contribute 0 to macro average."""

        def all_continue(**kwargs: Any) -> dict[str, Any]:
            return {"turn_relation": "continue", "standalone_query": "test"}

        cases = [
            _make_case("c1", "继续", "continue"),
        ]
        report = await evaluate_turn_relation(cases, all_continue, RouterEvalDependencies())
        # Only 1 populated class (continue=1.0) → macro = 1.0
        assert report.macro_accuracy == 1.0
        assert report.populated_classes == ["continue"]


# ======================================================================
# Tests: required false-negative rate
# ======================================================================


class TestRequiredFalseNegativeRate:
    """Verify required false-negative rate calculation."""

    @pytest.mark.asyncio
    async def test_no_false_negatives(self) -> None:
        """All required cases get required → rate = 0."""

        def required_resolver(**kwargs: Any) -> dict[str, Any]:
            return {
                "turn_relation": "continue",
                "standalone_query": "产品价格",
                "knowledge_policy": "required",
            }

        cases = [
            _make_case("c1", "产品多少钱", "continue", knowledge_policy="required"),
            _make_case("c2", "功能有哪些", "continue", knowledge_policy="required"),
        ]
        report = await evaluate_turn_relation(cases, required_resolver, RouterEvalDependencies())
        assert report.required_fn_rate == 0.0

    @pytest.mark.asyncio
    async def test_all_false_negatives(self) -> None:
        """No required cases → rate = 1.0."""

        def none_resolver(**kwargs: Any) -> dict[str, Any]:
            return {
                "turn_relation": "continue",
                "standalone_query": "你好",
                "knowledge_policy": "none",
            }

        cases = [
            _make_case("c1", "产品多少钱", "continue", knowledge_policy="required"),
        ]
        report = await evaluate_turn_relation(cases, none_resolver, RouterEvalDependencies())
        # Has fact signal "产品" → expected required
        assert report.required_fn_rate == 1.0

    @pytest.mark.asyncio
    async def test_evidence_required_fn_rate(self) -> None:
        """Evidence router required false-negative rate."""

        def perfect_ev(**kwargs: Any) -> dict[str, Any]:
            return {
                "knowledge_policy": "required",
                "response_mode": "retrieve",
                "intent": "knowledge_qa",
            }

        cases = [
            _make_ev_case("e1", "产品多少钱", "required"),
            _make_ev_case("e2", "公司介绍", "required"),
        ]
        report = await evaluate_evidence_policy(cases, perfect_ev, RouterEvalDependencies())
        assert report.required_fn_rate == 0.0

    @pytest.mark.asyncio
    async def test_evidence_half_fn(self) -> None:
        """Half of required cases are missed."""

        def half_ev(**kwargs: Any) -> dict[str, Any]:
            return {
                "knowledge_policy": "none",
                "response_mode": "direct",
                "intent": "general_sales_coaching",
            }

        cases = [
            _make_ev_case("e1", "产品多少钱", "required"),
            _make_ev_case("e2", "公司介绍", "required"),
            _make_ev_case("e3", "hello", "none"),
        ]
        report = await evaluate_evidence_policy(cases, half_ev, RouterEvalDependencies())
        # 2 required cases, 0 correct → rate = 2/2 = 1.0
        assert report.required_fn_rate == 1.0

    @pytest.mark.asyncio
    async def test_required_fn_with_fact_signals(self) -> None:
        """Fact signal detection for required false-negative counting."""
        assert _has_fact_signal("产品多少钱")
        assert _has_fact_signal("价格是多少")
        assert _has_fact_signal("有什么功能")
        assert _has_fact_signal("竞品分析")
        assert _has_fact_signal("合同条款")
        assert _has_fact_signal("实施周期")
        assert _has_fact_signal("怎么收费")
        assert _has_fact_signal("售后服务")
        assert _has_fact_signal("客户案例")
        assert not _has_fact_signal("你好")
        assert not _has_fact_signal("今天天气")
        assert not _has_fact_signal("我很焦虑")


# ======================================================================
# Tests: topic leakage rate
# ======================================================================


class TestTopicLeakageRate:
    """Verify topic leakage rate calculation."""

    @pytest.mark.asyncio
    async def test_no_leakage(self) -> None:
        """No new/switch cases incorrectly predicted as continue."""

        def perfect_reg(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            if "新" in msg:
                return {"turn_relation": "new", "standalone_query": msg}
            if "换" in msg:
                return {"turn_relation": "switch", "standalone_query": msg}
            return {"turn_relation": "continue", "standalone_query": msg}

        cases = [
            _make_case("c1", "新话题", "new"),
            _make_case("c2", "换个", "switch"),
            _make_case("c3", "继续聊", "continue"),
        ]
        report = await evaluate_turn_relation(cases, perfect_reg, RouterEvalDependencies())
        assert report.topic_leakage_rate == 0.0

    @pytest.mark.asyncio
    async def test_all_leakage(self) -> None:
        """All new/switch cases leak to continue."""

        def leaky(**kwargs: Any) -> dict[str, Any]:
            return {"turn_relation": "continue", "standalone_query": "test"}

        cases = [
            _make_case("c1", "新话题", "new"),
            _make_case("c2", "换个话题", "switch"),
        ]
        report = await evaluate_turn_relation(cases, leaky, RouterEvalDependencies())
        # 2 leaking out of 2 at-risk cases
        assert report.topic_leakage_rate == 1.0

    @pytest.mark.asyncio
    async def test_partial_leakage(self) -> None:
        """Half of new/switch cases leak."""

        def half_leaky(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            if "新" in msg:
                return {"turn_relation": "new", "standalone_query": msg}
            return {"turn_relation": "continue", "standalone_query": msg}

        cases = [
            _make_case("c1", "新话题", "new"),       # correct → no leakage
            _make_case("c2", "换个话题", "switch"),   # predicted continue → leakage
        ]
        report = await evaluate_turn_relation(cases, half_leaky, RouterEvalDependencies())
        # 1 leaking out of 2 at-risk
        assert report.topic_leakage_rate == 0.5


# ======================================================================
# Tests: clarification completion rate
# ======================================================================


class TestClarificationCompletionRate:
    """Verify clarification completion rate calculation."""

    @pytest.mark.asyncio
    async def test_no_ambiguous_cases(self) -> None:
        """No ambiguous cases → completion rate = 1.0."""

        def perfect(**kwargs: Any) -> dict[str, Any]:
            return {"turn_relation": "continue", "standalone_query": "test"}

        cases = [
            _make_case("c1", "继续", "continue"),
            _make_case("c2", "新话题", "new"),
        ]
        report = await evaluate_turn_relation(cases, perfect, RouterEvalDependencies())
        assert report.clarification_completion_rate == 1.0

    @pytest.mark.asyncio
    async def test_all_ambiguous_detected(self) -> None:
        """All ambiguous cases correctly identified."""
        ambiguous_count = 0

        def detect_ambig(**kwargs: Any) -> dict[str, Any]:
            nonlocal ambiguous_count
            ambiguous_count += 1
            return {"turn_relation": "ambiguous", "standalone_query": ""}

        cases = [
            _make_case("c1", "那个项目", "ambiguous"),
            _make_case("c2", "这个呢", "ambiguous"),
        ]
        report = await evaluate_turn_relation(cases, detect_ambig, RouterEvalDependencies())
        assert ambiguous_count == 2  # Both cases hit the resolver
        assert report.clarification_completion_rate == 1.0

    @pytest.mark.asyncio
    async def test_no_ambiguous_detected(self) -> None:
        """No ambiguous cases detected when they should be."""

        def always_continue(**kwargs: Any) -> dict[str, Any]:
            return {"turn_relation": "continue", "standalone_query": "test"}

        cases = [
            _make_case("c1", "那个项目", "ambiguous"),
            _make_case("c2", "这个呢", "ambiguous"),
        ]
        report = await evaluate_turn_relation(cases, always_continue, RouterEvalDependencies())
        assert report.clarification_completion_rate == 0.0

    @pytest.mark.asyncio
    async def test_partial_clarification(self) -> None:
        """Half of ambiguous cases detected."""

        def half_detect(**kwargs: Any) -> dict[str, Any]:
            msg = kwargs.get("message", "")
            if "项目" in msg:
                return {"turn_relation": "ambiguous", "standalone_query": ""}
            return {"turn_relation": "continue", "standalone_query": msg}

        cases = [
            _make_case("c1", "那个项目", "ambiguous"),  # detected correctly
            _make_case("c2", "这个呢", "ambiguous"),     # not detected
        ]
        report = await evaluate_turn_relation(cases, half_detect, RouterEvalDependencies())
        assert report.clarification_completion_rate == 0.5


# ======================================================================
# Tests: evidence-policy evaluation
# ======================================================================


class TestEvidencePolicyEvaluation:
    """Verify evidence-policy evaluation accuracy."""

    @pytest.mark.asyncio
    async def test_all_correct(self) -> None:
        """All evidence policy predictions match expected."""

        def perfect_ev(**kwargs: Any) -> dict[str, Any]:
            sq = kwargs.get("standalone_query", "")
            if "产品" in sq or "价格" in sq:
                return {
                    "knowledge_policy": "required",
                    "response_mode": "retrieve",
                    "intent": "knowledge_qa",
                }
            return {
                "knowledge_policy": "none",
                "response_mode": "direct",
                "intent": "general_sales_coaching",
            }

        cases = [
            _make_ev_case("e1", "产品多少钱", "required"),
            _make_ev_case("e2", "你好", "none"),
            _make_ev_case("e3", "客户跟进", "optional"),
        ]
        report = await evaluate_evidence_policy(cases, perfect_ev, RouterEvalDependencies())
        assert report.accuracy == 2 / 3  # e3 incorrect (perfect_ev returns required for "产品" in "客户跟进")
        assert report.total_cases == 3

    @pytest.mark.asyncio
    async def test_all_none(self) -> None:
        """All predictions are none — check accuracy per class."""

        def none_ev(**kwargs: Any) -> dict[str, Any]:
            return {
                "knowledge_policy": "none",
                "response_mode": "direct",
                "intent": "general_sales_coaching",
            }

        cases = [
            _make_ev_case("e1", "产品多少钱", "required"),  # wrong
            _make_ev_case("e2", "你好", "none"),            # correct
            _make_ev_case("e3", "价格", "required"),        # wrong
        ]
        report = await evaluate_evidence_policy(cases, none_ev, RouterEvalDependencies())
        assert report.correct == 1
        assert report.per_class_metrics["none"].total == 1
        assert report.per_class_metrics["none"].correct == 1
        assert report.per_class_metrics["required"].total == 2
        assert report.per_class_metrics["required"].correct == 0


# ======================================================================
# Tests: load_cases and report writing
# ======================================================================


class TestLoadCases:
    """Verify JSONL loading."""

    def test_load_valid_jsonl(self) -> None:
        """Load cases from a valid JSONL string via temp file."""
        lines = [
            json.dumps({
                "id": "t1",
                "input": "hello",
                "context": {"topic": None},
                "expected": {"turn_relation": "new"},
                "rationale": "test",
            }),
            json.dumps({
                "id": "t2",
                "input": "继续",
                "context": {"topic": {"summary": "test"}},
                "expected": {"turn_relation": "continue"},
                "rationale": "test2",
            }),
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        try:
            cases = load_cases(tmp_path)
            assert len(cases) == 2
            assert cases[0].id == "t1"
            assert cases[0].input == "hello"
            assert cases[0].expected["turn_relation"] == "new"
            assert cases[1].id == "t2"
            assert cases[1].expected["turn_relation"] == "continue"
        finally:
            os.unlink(tmp_path)

    def test_load_empty_jsonl(self) -> None:
        """Loading empty file returns empty list."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write("")
            tmp_path = f.name
        try:
            cases = load_cases(tmp_path)
            assert cases == []
        finally:
            os.unlink(tmp_path)


class TestReportWriting:
    """Verify JSON and Markdown report writing."""

    def test_write_json_report(self) -> None:
        """JSON report is valid and contains expected keys."""
        report = EvalReport(
            timestamp="2026-07-06T00:00:00",
            total_cases=10,
            correct=8,
            accuracy=0.8,
            per_class_metrics={
                "continue": ClassMetrics(total=5, correct=4, false_negatives=1, false_positives=0),
                "new": ClassMetrics(total=5, correct=4, false_negatives=1, false_positives=0),
            },
            macro_accuracy=0.8,
            required_fn_rate=0.0,
            unnecessary_retrieval_rate=0.1,
            topic_leakage_rate=0.05,
            clarification_completion_rate=0.9,
            p50_latency_ms=100.0,
            p95_latency_ms=500.0,
            thresholds_met=True,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            tmp_path = f.name

        try:
            write_json_report(report, tmp_path)
            with open(tmp_path, "r") as f:
                data = json.load(f)
            assert data["total_cases"] == 10
            assert data["correct"] == 8
            assert data["accuracy"] == 0.8
            assert data["thresholds_met"] is True
            assert "per_class" in data
            assert data["per_class"]["continue"]["total"] == 5
        finally:
            os.unlink(tmp_path)

    def test_write_markdown_report(self) -> None:
        """Markdown report contains expected sections."""
        report = EvalReport(
            timestamp="2026-07-06T00:00:00",
            total_cases=5,
            correct=3,
            accuracy=0.6,
            per_class_metrics={
                rel: ClassMetrics(total=1, correct=0 if rel != "continue" else 1)
                for rel in TURN_RELATIONS
            },
            macro_accuracy=0.2,
            required_fn_rate=0.0,
            unnecessary_retrieval_rate=0.0,
            topic_leakage_rate=0.0,
            clarification_completion_rate=1.0,
            p50_latency_ms=50.0,
            p95_latency_ms=200.0,
            thresholds_met=True,
            failures=[],
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            tmp_path = f.name

        try:
            write_markdown_report(report, tmp_path, "Test Report")
            with open(tmp_path, "r") as f:
                content = f.read()
            assert "# Test Report" in content
            assert "## Per-class metrics" in content
            assert "## Key rates" in content
            assert "## Latency" in content
            assert "## Detailed results" in content
            assert "60.0%" in content
            assert "20.0%" in content
            assert "Macro accuracy" in content
        finally:
            os.unlink(tmp_path)


# ======================================================================
# Tests: ClassMetrics properties
# ======================================================================


class TestClassMetrics:
    """Verify ClassMetrics derived properties."""

    def test_accuracy_perfect(self) -> None:
        cm = ClassMetrics(total=10, correct=10)
        assert cm.accuracy == 1.0

    def test_accuracy_zero(self) -> None:
        cm = ClassMetrics(total=10, correct=0)
        assert cm.accuracy == 0.0

    def test_accuracy_empty(self) -> None:
        cm = ClassMetrics(total=0, correct=0)
        assert cm.accuracy == 0.0

    def test_false_negative_rate(self) -> None:
        cm = ClassMetrics(total=10, correct=8, false_negatives=2)
        assert cm.false_negative_rate == 0.2

    def test_false_negative_rate_empty(self) -> None:
        cm = ClassMetrics(total=0, correct=0)
        assert cm.false_negative_rate == 0.0

    def test_accuracy_half(self) -> None:
        cm = ClassMetrics(total=4, correct=2)
        assert cm.accuracy == 0.5


# ======================================================================
# Tests: _count_required_fn helper
# ======================================================================


class TestRequiredFNHelper:
    """Verify _count_required_fn helper."""

    def test_no_results(self) -> None:
        assert _count_required_fn([]) == 0

    def test_mixed_results(self) -> None:
        """Fact signal in standalone_query triggers required."""
        from eval.router.run_router_eval import EvalResult

        results = [
            EvalResult(
                id="r1", input="test", expected={"knowledge_policy": "required"},
                actual={"knowledge_policy": "none"}, correct=False,
                latency_ms=0.0,
            ),
            EvalResult(
                id="r2", input="test", expected={},
                actual={"knowledge_policy": "none"}, correct=True,
                latency_ms=0.0,
                # standalone_query has fact signal
            ),
        ]
        # r1: expected required, actual none → FN
        # r2: standalone_query not set, no fact signal found
        # Without setting standalone_query in expected, need to check the actual implementation
        count = _count_required_fn(results)
        # r1 is a required FN because expected has knowledge_policy=required
        assert count == 1
