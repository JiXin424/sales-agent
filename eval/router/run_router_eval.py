"""Bounded Router Evaluation Runner.

Loads turn-relation and evidence-policy evaluation datasets,
invokes injectable resolver functions, computes accuracy and
latency metrics, and writes reports.

Usage:
    # Fixture mode (offline, no model calls)
    python eval/router/run_router_eval.py --fixture-mode

    # Model-backed evaluation
    python eval/router/run_router_eval.py --output eval/results/router

Exit codes:
    0 - All thresholds met
    1 - Threshold failure
    2 - Data loading error
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

TURN_RELATION_ACCURACY_THRESHOLD = 0.90
REQUIRED_FALSE_NEGATIVE_RATE_THRESHOLD = 0.02
TOPIC_LEAKAGE_RATE_THRESHOLD = 0.01

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TURN_RELATIONS = ["continue", "revise", "switch", "new", "ambiguous"]
EVIDENCE_POLICIES = ["none", "optional", "required"]

# ---------------------------------------------------------------------------
# Resolver function signatures (type aliases)
# ---------------------------------------------------------------------------

ContextResolverFn = Callable[..., dict[str, Any]]
EvidenceRouterFn = Callable[..., dict[str, Any]]

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    """A single evaluation case loaded from JSONL."""

    id: str
    input: str
    context: dict[str, Any]
    expected: dict[str, Any]
    rationale: str


@dataclass
class EvalResult:
    """The result of evaluating a single case."""

    id: str
    input: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    correct: bool
    latency_ms: float
    error: str | None = None


@dataclass
class ClassMetrics:
    """Per-class accuracy metrics."""

    total: int = 0
    correct: int = 0
    false_negatives: int = 0
    false_positives: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0

    @property
    def false_negative_rate(self) -> float:
        return self.false_negatives / self.total if self.total > 0 else 0.0


@dataclass
class EvalReport:
    """Complete evaluation report."""

    timestamp: str
    total_cases: int
    correct: int
    accuracy: float
    per_class_metrics: dict[str, ClassMetrics]
    macro_accuracy: float
    required_fn_rate: float
    unnecessary_retrieval_rate: float
    topic_leakage_rate: float
    clarification_completion_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    thresholds_met: bool
    failures: list[str] = field(default_factory=list)
    results: list[EvalResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------


def load_cases(path: str) -> list[EvalCase]:
    """Load evaluation cases from a JSONL file."""
    cases: list[EvalCase] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            cases.append(
                EvalCase(
                    id=obj["id"],
                    input=obj["input"],
                    context=obj.get("context", {}),
                    expected=obj["expected"],
                    rationale=obj.get("rationale", ""),
                )
            )
    return cases


# ---------------------------------------------------------------------------
# Fixture-mode resolver stubs
# ---------------------------------------------------------------------------


def _fixture_context_resolver(
    message: str, context: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    """Stub context resolver for fixture mode — always returns continue."""
    return {
        "turn_relation": "continue",
        "standalone_query": message,
        "retained_entities": [],
        "retracted_goals": [],
        "missing_references": [],
        "confidence": 0.95,
        "reason_code": "fixture_mode",
    }


def _fixture_evidence_router(
    standalone_query: str, **kwargs: Any
) -> dict[str, Any]:
    """Stub evidence router for fixture mode — always returns none/direct."""
    return {
        "intent": "general_sales_coaching",
        "response_mode": "direct",
        "knowledge_policy": "none",
        "knowledge_scope": [],
        "retrieval_query": None,
        "confidence": 0.95,
        "reason_code": "fixture_mode",
    }


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------


def evaluate_turn_relation(
    cases: list[EvalCase],
    resolver_fn: ContextResolverFn,
    resolver_kwargs: dict[str, Any] | None = None,
) -> EvalReport:
    """Evaluate turn-relation classification accuracy."""
    results: list[EvalResult] = []
    per_class: dict[str, ClassMetrics] = {r: ClassMetrics() for r in TURN_RELATIONS}

    for case in cases:
        start = time.perf_counter()
        error: str | None = None
        actual: dict[str, Any] = {}
        try:
            actual = resolver_fn(
                message=case.input,
                context=case.context,
                **(resolver_kwargs or {}),
            )
        except Exception as e:
            error = str(e)
        elapsed = (time.perf_counter() - start) * 1000

        expected_relation = case.expected.get("turn_relation", "")
        actual_relation = actual.get("turn_relation", "") if actual else ""

        correct = (actual_relation == expected_relation) and error is None

        result = EvalResult(
            id=case.id,
            input=case.input,
            expected=case.expected,
            actual=actual,
            correct=correct,
            latency_ms=round(elapsed, 1),
            error=error,
        )
        results.append(result)

        # Update per-class metrics
        if expected_relation in per_class:
            per_class[expected_relation].total += 1
            if correct:
                per_class[expected_relation].correct += 1

        # False negatives: expected X but got something else
        if expected_relation in per_class and not correct and error is None:
            per_class[expected_relation].false_negatives += 1

    return _build_turn_relation_report(results, per_class)


def _build_turn_relation_report(
    results: list[EvalResult],
    per_class: dict[str, ClassMetrics],
) -> EvalReport:
    """Build a complete EvalReport for turn-relation evaluation."""
    total = len(results)
    correct = sum(1 for r in results if r.correct)
    accuracy = correct / total if total > 0 else 0.0

    per_class_names = sorted(per_class.keys())
    macro_accuracy = (
        sum(per_class[cn].accuracy for cn in per_class_names) / len(per_class_names)
        if per_class_names
        else 0.0
    )

    # Required false-negative rate: cases expected=required but got != required
    # For turn-relation, this uses the standalone_query to detect fact signals
    required_fn_count = _count_required_fn(results)
    required_total = sum(
        1 for r in results
        if r.expected.get("knowledge_policy") == "required"
        or _has_fact_signal(r.expected.get("standalone_query", ""))
    )
    required_fn_rate = (
        required_fn_count / required_total if required_total > 0 else 0.0
    )

    # Unnecessary retrieval rate: expected not required but got required
    unnecessary_retrieval = sum(
        1 for r in results
        if r.expected.get("knowledge_policy", "none") in ("none", "optional")
        and r.actual.get("knowledge_policy") == "required"
    )
    unnecessary_total = sum(
        1 for r in results
        if r.expected.get("knowledge_policy", "none") in ("none", "optional")
    )
    unnecessary_retrieval_rate = (
        unnecessary_retrieval / unnecessary_total if unnecessary_total > 0 else 0.0
    )

    # Topic leakage: expected new/switch but predicted continue
    leakage_count = sum(
        1 for r in results
        if r.expected.get("turn_relation") in ("new", "switch")
        and r.actual.get("turn_relation") == "continue"
    )
    leakage_total = sum(
        1 for r in results
        if r.expected.get("turn_relation") in ("new", "switch")
    )
    topic_leakage_rate = leakage_count / leakage_total if leakage_total > 0 else 0.0

    # Clarification completion: expected ambiguous and correctly identified as ambiguous
    ambiguous_needed = sum(
        1 for r in results
        if r.expected.get("turn_relation") == "ambiguous"
    )
    ambiguous_detected = sum(
        1 for r in results
        if r.expected.get("turn_relation") == "ambiguous"
        and r.actual.get("turn_relation") == "ambiguous"
        and r.correct
    )
    clarification_completion_rate = (
        ambiguous_detected / ambiguous_needed if ambiguous_needed > 0 else 1.0
    )

    # Latencies
    latencies = sorted(r.latency_ms for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[p95_idx] if latencies else 0.0

    # Threshold checks
    failures: list[str] = []
    thresholds_met = True

    if accuracy < TURN_RELATION_ACCURACY_THRESHOLD:
        thresholds_met = False
        failures.append(
            f"Turn-relation accuracy {accuracy:.1%} < threshold "
            f"{TURN_RELATION_ACCURACY_THRESHOLD:.0%}"
        )

    if required_fn_rate > REQUIRED_FALSE_NEGATIVE_RATE_THRESHOLD:
        thresholds_met = False
        failures.append(
            f"Required false-negative rate {required_fn_rate:.1%} > threshold "
            f"{REQUIRED_FALSE_NEGATIVE_RATE_THRESHOLD:.0%}"
        )

    if topic_leakage_rate > TOPIC_LEAKAGE_RATE_THRESHOLD:
        thresholds_met = False
        failures.append(
            f"Topic leakage rate {topic_leakage_rate:.1%} > threshold "
            f"{TOPIC_LEAKAGE_RATE_THRESHOLD:.0%}"
        )

    return EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_cases=total,
        correct=correct,
        accuracy=accuracy,
        per_class_metrics=per_class,
        macro_accuracy=macro_accuracy,
        required_fn_rate=required_fn_rate,
        unnecessary_retrieval_rate=unnecessary_retrieval_rate,
        topic_leakage_rate=topic_leakage_rate,
        clarification_completion_rate=clarification_completion_rate,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        thresholds_met=thresholds_met,
        failures=failures,
        results=results,
    )


def evaluate_evidence_policy(
    cases: list[EvalCase],
    resolver_fn: EvidenceRouterFn,
    resolver_kwargs: dict[str, Any] | None = None,
) -> EvalReport:
    """Evaluate evidence policy routing accuracy."""
    results: list[EvalResult] = []
    per_class: dict[str, ClassMetrics] = {p: ClassMetrics() for p in EVIDENCE_POLICIES}

    for case in cases:
        start = time.perf_counter()
        error: str | None = None
        actual: dict[str, Any] = {}
        try:
            actual = resolver_fn(
                standalone_query=case.context.get("standalone_query", case.input),
                **(resolver_kwargs or {}),
            )
        except Exception as e:
            error = str(e)
        elapsed = (time.perf_counter() - start) * 1000

        expected_policy = case.expected.get("knowledge_policy", "")
        actual_policy = actual.get("knowledge_policy", "") if actual else ""

        correct = (actual_policy == expected_policy) and error is None

        result = EvalResult(
            id=case.id,
            input=case.input,
            expected=case.expected,
            actual=actual,
            correct=correct,
            latency_ms=round(elapsed, 1),
            error=error,
        )
        results.append(result)

        if expected_policy in per_class:
            per_class[expected_policy].total += 1
            if correct:
                per_class[expected_policy].correct += 1

        if expected_policy in per_class and not correct and error is None:
            per_class[expected_policy].false_negatives += 1

    return _build_evidence_report(results, per_class)


def _build_evidence_report(
    results: list[EvalResult],
    per_class: dict[str, ClassMetrics],
) -> EvalReport:
    """Build a complete EvalReport for evidence-policy evaluation."""
    total = len(results)
    correct = sum(1 for r in results if r.correct)
    accuracy = correct / total if total > 0 else 0.0

    per_class_names = sorted(per_class.keys())
    macro_accuracy = (
        sum(per_class[cn].accuracy for cn in per_class_names) / len(per_class_names)
        if per_class_names
        else 0.0
    )

    # Required false-negative: expected=required but got something else
    required_fn = sum(
        1 for r in results
        if r.expected.get("knowledge_policy") == "required"
        and r.actual.get("knowledge_policy") != "required"
    )
    required_total = sum(
        1 for r in results
        if r.expected.get("knowledge_policy") == "required"
    )
    required_fn_rate = required_fn / required_total if required_total > 0 else 0.0

    # Unnecessary retrieval: expected none/optional but got required
    unnecessary_retrieval = sum(
        1 for r in results
        if r.expected.get("knowledge_policy") in ("none", "optional")
        and r.actual.get("knowledge_policy") == "required"
    )
    unnecessary_total = sum(
        1 for r in results
        if r.expected.get("knowledge_policy") in ("none", "optional")
    )
    unnecessary_retrieval_rate = (
        unnecessary_retrieval / unnecessary_total if unnecessary_total > 0 else 0.0
    )

    # Topic leakage / clarification completion not applicable for evidence
    # Set to 0 / 1.0 as sentinels
    topic_leakage_rate = 0.0
    clarification_completion_rate = 1.0

    latencies = sorted(r.latency_ms for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[p95_idx] if latencies else 0.0

    failures: list[str] = []
    thresholds_met = True

    if required_fn_rate > REQUIRED_FALSE_NEGATIVE_RATE_THRESHOLD:
        thresholds_met = False
        failures.append(
            f"Evidence required false-negative rate {required_fn_rate:.1%} > "
            f"threshold {REQUIRED_FALSE_NEGATIVE_RATE_THRESHOLD:.0%}"
        )

    return EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_cases=total,
        correct=correct,
        accuracy=accuracy,
        per_class_metrics=per_class,
        macro_accuracy=macro_accuracy,
        required_fn_rate=required_fn_rate,
        unnecessary_retrieval_rate=unnecessary_retrieval_rate,
        topic_leakage_rate=topic_leakage_rate,
        clarification_completion_rate=clarification_completion_rate,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        thresholds_met=thresholds_met,
        failures=failures,
        results=results,
    )


def _count_required_fn(results: list[EvalResult]) -> int:
    """Count false negatives where retrieval was required but not triggered."""
    count = 0
    for r in results:
        expected_required = (
            r.expected.get("knowledge_policy") == "required"
            or _has_fact_signal(r.expected.get("standalone_query", ""))
        )
        if expected_required and r.actual.get("knowledge_policy") != "required":
            count += 1
    return count


def _has_fact_signal(text: str) -> bool:
    """Check if text contains high-risk enterprise-fact signals."""
    signals = [
        "产品", "功能", "方案", "服务",
        "公司", "企业",
        "价格", "多少钱", "怎么收费", "报价", "费用", "成本", "预算",
        "政策", "制度", "规定", "流程", "售后", "保障",
        "案例", "成功故事", "客户见证", "效果",
        "竞品", "竞争对手", "对比", "区别", "vs",
        "合同", "合约", "协议", "签约", "条款",
        "交付", "实施", "上线", "部署",
    ]
    return any(s in text for s in signals)


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def write_json_report(report: EvalReport, path: str) -> None:
    """Write evaluation report as JSON."""
    data = {
        "timestamp": report.timestamp,
        "total_cases": report.total_cases,
        "correct": report.correct,
        "accuracy": round(report.accuracy, 4),
        "macro_accuracy": round(report.macro_accuracy, 4),
        "per_class": {
            cls: {
                "total": m.total,
                "correct": m.correct,
                "accuracy": round(m.accuracy, 4),
                "false_negative_rate": round(m.false_negative_rate, 4),
            }
            for cls, m in sorted(report.per_class_metrics.items())
        },
        "required_false_negative_rate": round(report.required_fn_rate, 4),
        "unnecessary_retrieval_rate": round(report.unnecessary_retrieval_rate, 4),
        "topic_leakage_rate": round(report.topic_leakage_rate, 4),
        "clarification_completion_rate": round(report.clarification_completion_rate, 4),
        "p50_latency_ms": report.p50_latency_ms,
        "p95_latency_ms": report.p95_latency_ms,
        "thresholds_met": report.thresholds_met,
        "failures": report.failures,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_markdown_report(
    report: EvalReport, path: str, title: str
) -> None:
    """Write evaluation report as Markdown."""
    lines = [
        f"# {title}",
        "",
        f"**Timestamp:** {report.timestamp}",
        f"**Total cases:** {report.total_cases}",
        f"**Correct:** {report.correct}",
        f"**Accuracy:** {report.accuracy:.1%}",
        f"**Macro accuracy:** {report.macro_accuracy:.1%}",
        f"**Thresholds met:** {'Yes' if report.thresholds_met else 'No'}",
        "",
        "## Per-class metrics",
        "",
        "| Class | Total | Correct | Accuracy | False-negative rate |",
        "|-------|-------|---------|----------|--------------------|",
    ]
    for cls in sorted(report.per_class_metrics.keys()):
        m = report.per_class_metrics[cls]
        lines.append(
            f"| {cls} | {m.total} | {m.correct} | {m.accuracy:.1%} | "
            f"{m.false_negative_rate:.1%} |"
        )

    lines.extend([
        "",
        "## Key rates",
        "",
        f"- **Required false-negative rate:** {report.required_fn_rate:.1%}",
        f"- **Unnecessary retrieval rate:** {report.unnecessary_retrieval_rate:.1%}",
        f"- **Topic leakage rate:** {report.topic_leakage_rate:.1%}",
        f"- **Clarification completion rate:** "
        f"{report.clarification_completion_rate:.1%}",
        "",
        "## Latency",
        "",
        f"- **P50:** {report.p50_latency_ms} ms",
        f"- **P95:** {report.p95_latency_ms} ms",
        "",
    ])

    if report.failures:
        lines.extend([
            "## Threshold failures",
            "",
        ])
        for f in report.failures:
            lines.append(f"- {f}")
        lines.append("")

    # Detailed results table
    lines.extend([
        "## Detailed results",
        "",
        "| ID | Correct | Expected | Actual | Latency (ms) | Error |",
        "|----|---------|----------|--------|---------------|-------|",
    ])
    for r in report.results[:100]:
        exp_val = _get_expected_key(r.expected)
        act_val = _get_actual_key(r.actual)
        lines.append(
            f"| {r.id} | {'PASS' if r.correct else 'FAIL'} | "
            f"{exp_val} | {act_val} | {r.latency_ms} | {r.error or ''} |"
        )

    if len(report.results) > 100:
        lines.append(
            f"| ... | ({len(report.results) - 100} more rows) | | | | |"
        )

    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _get_expected_key(expected: dict[str, Any]) -> str:
    for key in ("turn_relation", "knowledge_policy"):
        if key in expected:
            return str(expected[key])
    return str(expected)


def _get_actual_key(actual: dict[str, Any]) -> str:
    for key in ("turn_relation", "knowledge_policy"):
        if key in actual:
            return str(actual[key])
    return str(actual)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Bounded Router Evaluation Runner"
    )
    parser.add_argument(
        "--turn-relation-cases",
        default="eval/router/turn_relation_cases.jsonl",
        help="Path to turn-relation evaluation cases JSONL",
    )
    parser.add_argument(
        "--evidence-cases",
        default="eval/router/evidence_policy_cases.jsonl",
        help="Path to evidence policy evaluation cases JSONL",
    )
    parser.add_argument(
        "--fixture-mode",
        action="store_true",
        help="Run with fixture stubs (no model calls)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for reports",
    )
    parser.add_argument(
        "--context-resolver-fn",
        default=None,
        help="Dotted path to context resolver function (e.g. 'module.fn')",
    )
    parser.add_argument(
        "--evidence-router-fn",
        default=None,
        help="Dotted path to evidence router function",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run evaluation and return exit code."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    fixture_mode = args.fixture_mode

    # Set up resolver functions
    if fixture_mode:
        context_resolver = _fixture_context_resolver
        evidence_router = _fixture_evidence_router
        logger.info("Running in FIXTURE mode — using stub resolvers")
    else:
        if args.context_resolver_fn:
            context_resolver = _import_fn(args.context_resolver_fn)
            logger.info("Using custom context resolver: %s", args.context_resolver_fn)
        else:
            from sales_agent.services.context_resolver import resolve_context
            context_resolver = resolve_context
            logger.info("Using production context resolver")

        if args.evidence_router_fn:
            evidence_router = _import_fn(args.evidence_router_fn)
            logger.info("Using custom evidence router: %s", args.evidence_router_fn)
        else:
            from sales_agent.services.evidence_router import route_intent_evidence
            evidence_router = route_intent_evidence
            logger.info("Using production evidence router")

    # Load cases
    turn_cases = load_cases(args.turn_relation_cases)
    ev_cases = load_cases(args.evidence_cases)
    logger.info("Loaded %d turn-relation cases", len(turn_cases))
    logger.info("Loaded %d evidence-policy cases", len(ev_cases))

    if not turn_cases and not ev_cases:
        logger.error("No cases loaded — exiting")
        return 2

    # Evaluate turn relation
    logger.info("Evaluating turn-relation classification...")
    tr_report = evaluate_turn_relation(turn_cases, context_resolver)
    logger.info(
        "Turn-relation accuracy: %.1f%% (%d/%d)",
        tr_report.accuracy * 100,
        tr_report.correct,
        tr_report.total_cases,
    )

    # Evaluate evidence policy
    logger.info("Evaluating evidence-policy routing...")
    ev_report = evaluate_evidence_policy(ev_cases, evidence_router)
    logger.info(
        "Evidence-policy accuracy: %.1f%% (%d/%d)",
        ev_report.accuracy * 100,
        ev_report.correct,
        ev_report.total_cases,
    )

    # Write reports if output directory specified
    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)

        write_json_report(
            tr_report, str(out_dir / "turn_relation_report.json")
        )
        write_markdown_report(
            tr_report,
            str(out_dir / "turn_relation_report.md"),
            "Turn-Relation Classification Report",
        )

        write_json_report(
            ev_report, str(out_dir / "evidence_policy_report.json")
        )
        write_markdown_report(
            ev_report,
            str(out_dir / "evidence_policy_report.md"),
            "Evidence-Policy Routing Report",
        )

        logger.info("Reports written to %s", out_dir)

    # Summary
    all_met = tr_report.thresholds_met and ev_report.thresholds_met

    print()
    print("=" * 60)
    print("ROUTER EVALUATION SUMMARY")
    print("=" * 60)
    print(
        f"  Turn-relation: {tr_report.correct}/{tr_report.total_cases} "
        f"({tr_report.accuracy:.1%}) "
        f"-- {'PASS' if tr_report.thresholds_met else 'FAIL'}"
    )
    print(
        f"  Evidence-policy: {ev_report.correct}/{ev_report.total_cases} "
        f"({ev_report.accuracy:.1%}) "
        f"-- {'PASS' if ev_report.thresholds_met else 'FAIL'}"
    )
    print(
        f"  Required FN rate: {tr_report.required_fn_rate:.1%} | "
        f"Topic leakage: {tr_report.topic_leakage_rate:.1%}"
    )
    print(
        f"  Overall thresholds: {'MET' if all_met else 'NOT MET'}"
    )
    if not all_met:
        for f in tr_report.failures:
            print(f"  FAIL: {f}")
        for f in ev_report.failures:
            print(f"  FAIL: {f}")
    print("=" * 60)

    return 0 if all_met else 1


def _import_fn(dotted: str) -> Any:
    """Import a function from a dotted path (e.g. 'package.module.fn')."""
    parts = dotted.split(".")
    module_path = ".".join(parts[:-1])
    fn_name = parts[-1]
    module = importlib.import_module(module_path)
    return getattr(module, fn_name)


if __name__ == "__main__":
    sys.exit(main())
