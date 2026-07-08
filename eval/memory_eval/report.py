"""Evaluation report aggregation + JSON/Markdown writer (Spec 4 §7)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from eval.memory_eval.metrics.types import ConfusionMatrix, MetricResult
from eval.memory_eval.versions import VersionBundle

GROUP_ORDER = [
    "deterministic_state_safety",
    "semantic_answer_quality",
    "trajectory",
    "persistence_isolation",
    "latency_cost",
    "scenario_slice",
]


@dataclass
class EvaluationReport:
    versions: VersionBundle
    total_scenarios: int = 0
    total_turns: int = 0
    thresholds_met: bool = True
    groups: dict[str, list[MetricResult]] = field(default_factory=dict)
    confusion_matrices: dict[str, ConfusionMatrix] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def add_metric(self, group: str, result: MetricResult) -> None:
        self.groups.setdefault(group, []).append(result)
        if not result.passes:
            self.thresholds_met = False
            self.failures.append(f"{group}/{result.name}: {result.score} < {result.threshold}")

    def add_confusion(self, name: str, cm: ConfusionMatrix) -> None:
        self.confusion_matrices[name] = cm


def _metric_to_dict(m: MetricResult) -> dict:
    d = {
        "name": m.name,
        "applicable": m.applicable,
        "numerator": m.numerator,
        "denominator": m.denominator,
        "score": round(m.score, 6),
        "threshold": m.threshold,
        "error": m.error,
        "sample_ids": m.sample_ids,
    }
    for extra in ("recall", "f1"):
        if hasattr(m, extra):
            d[extra] = round(getattr(m, extra), 6)
    return d


def write_report(report: EvaluationReport, out_dir: str) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    data = {
        "versions": report.versions.to_dict(),
        "total_scenarios": report.total_scenarios,
        "total_turns": report.total_turns,
        "thresholds_met": report.thresholds_met,
        "groups": {
            g: [_metric_to_dict(m) for m in report.groups.get(g, [])]
            for g in GROUP_ORDER
            if g in report.groups
        },
        "confusion_matrices": {
            name: cm.to_dict() for name, cm in report.confusion_matrices.items()
        },
        "failures": report.failures,
    }
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}  Turns: {report.total_turns}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        "",
        "## Versions",
        "",
    ]
    for k, v in report.versions.to_dict().items():
        md.append(f"- {k}: {v}")
    md += ["", "## Metrics", ""]
    for group in GROUP_ORDER:
        for m in report.groups.get(group, []):
            flag = "PASS" if m.passes else "FAIL"
            md.append(
                f"- [{group}] {m.name}: {m.score:.3f} "
                f"({m.numerator}/{m.denominator}, applicable={m.applicable}) [{flag}]"
                + (f" err={m.error}" if m.error else "")
            )
    if report.failures:
        md += ["", "## Failures", ""]
        md += [f"- {f}" for f in report.failures]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out


__all__ = ["EvaluationReport", "write_report"]
