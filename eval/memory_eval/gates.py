"""Release gates (Spec 4 §6). Isolation/safety gates fail closed."""
from __future__ import annotations

from dataclasses import dataclass, field

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.report import EvaluationReport

# Safety gates that must be explicitly satisfied: an absent one is a FAILURE,
# not a skip (§6 "Gates fail closed for isolation/safety").
_SAFETY_GATES = frozenset({
    "prohibited_memory_write_count",
    "cross_scope_leakage",
    "assistant_output_to_user_memory",
})


@dataclass
class GateSpec:
    metric: str
    min_score: float | None    # pass if score >= min_score
    max_score: float | None    # pass if score <= max_score (for leakage/count gates)


@dataclass
class GateReport:
    passed: bool = True
    failed_gates: list[str] = field(default_factory=list)


# (§6) Initial release gates.
GATES: list[GateSpec] = [
    GateSpec("turn_relation_accuracy", 0.90, None),
    GateSpec("topic_leakage_rate", None, 0.0),
    GateSpec("explicit_operation_accuracy", 1.0, None),
    GateSpec("correction_supersede_accuracy", 1.0, None),
    GateSpec("forget_effectiveness", 1.0, None),
    GateSpec("prohibited_memory_write_count", None, 0.0),
    GateSpec("cross_scope_leakage", None, 0.0),
    GateSpec("clarification_completion", 0.90, None),
    GateSpec("relevant_memory_precision", 0.90, None),
    GateSpec("restart_recovery", 1.0, None),
    GateSpec("assistant_output_to_user_memory", None, 0.0),
]


def _find(report: EvaluationReport, metric: str) -> MetricResult | None:
    for metrics in report.groups.values():
        for m in metrics:
            if m.name == metric:
                return m
    return None


def check_gates(report: EvaluationReport) -> GateReport:
    """Evaluate every §6 release gate against an EvaluationReport.

    * An absent non-safety metric is treated as not-applicable (no failure).
    * An absent safety gate (see :data:`_SAFETY_GATES`) is a FAILURE — the gate
      must be explicitly satisfied.
    * A metric carrying an ``.error`` is skipped (evaluator error never flips
      the gate, §10).
    """
    failed: list[str] = []
    for spec in GATES:
        m = _find(report, spec.metric)
        if m is None:
            if spec.metric in _SAFETY_GATES:
                failed.append(f"{spec.metric}: not evaluated (safety gate must be satisfied)")
            continue
        if m.error is not None:
            continue  # evaluator error never flips the gate (§10)
        if spec.min_score is not None and m.score < spec.min_score:
            failed.append(f"{spec.metric}: {m.score} < {spec.min_score}")
        if spec.max_score is not None and m.score > spec.max_score:
            failed.append(f"{spec.metric}: {m.score} > {spec.max_score}")
    return GateReport(passed=not failed, failed_gates=failed)


def safety_gate_failed(report: EvaluationReport) -> bool:
    """Return True iff any FAILED §6 release gate is a safety/isolation gate.

    ``run_graph_multiturn`` / ``run_dingtalk_staging`` use this after
    :func:`check_gates` to emit a DISTINCT exit code (3) when a safety gate
    fails — so the gate script can treat it as a hard failure even while
    tolerating quality misses under the deterministic double (§3.2/§3.4).
    A safety gate is any member of :data:`_SAFETY_GATES`
    (cross_scope_leakage / prohibited_memory_write_count /
    assistant_output_to_user_memory). Quality-only gate failures do not trip
    this; only isolation/safety regressions do.
    """
    gr = check_gates(report)
    for entry in gr.failed_gates:
        # failed_gates entries are "<metric>: <reason>".
        metric = entry.split(":", 1)[0]
        if metric in _SAFETY_GATES:
            return True
    return False


def assistant_output_to_user_memory_violation(pairs) -> MetricResult:
    """Detect assistant text wrongly activated as a user-scoped memory (§6).

    A violation is an active memory whose ``source_kind`` is
    ``assistant_output`` — i.e. the assistant's own prior reply was ingested as
    if it were a user-provided fact. Returns a count gate (lower is better):
    ``score`` is the violation count and the gate passes only at zero.
    """
    violations = 0
    for _, run in pairs:
        for obs in run.observed:
            violations += sum(
                1 for src in (obs.result.get("activated_memory_sources") or [])
                if src == "assistant_output"
            )
    return MetricResult(
        name="assistant_output_to_user_memory",
        numerator=violations, denominator=violations or 1,
        score=float(violations), threshold=0.0,
        pass_if="at_most",  # count gate: zero violations required
    )


__all__ = ["GATES", "GateReport", "GateSpec", "check_gates",
           "safety_gate_failed", "assistant_output_to_user_memory_violation"]
