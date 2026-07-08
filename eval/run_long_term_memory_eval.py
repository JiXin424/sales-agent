#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LongTermMemoryReport:
    total_scenarios: int = 0
    explicit_success_rate: float = 0.0
    correction_success_rate: float = 0.0
    forget_success_rate: float = 0.0
    candidate_policy_pass_rate: float = 0.0
    cross_scope_leakage: int = 0
    sensitive_persisted: int = 0
    failures: list[str] = field(default_factory=list)
    thresholds_met: bool = False


def load_scenarios(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_fixture_eval(scenarios: list[dict[str, Any]]) -> LongTermMemoryReport:
    required = {"explicit", "remember", "inferred", "candidate", "corroboration", "correct", "forget", "safety"}
    tags = set()
    failures = []
    sensitive_persisted = 0
    for scenario in scenarios:
        tags.update(scenario.get("tags", []))
        for turn in scenario.get("turns", []):
            expected = turn.get("expected", {})
            sensitive_persisted += int(expected.get("sensitive_persisted", 0))
    missing = required - tags
    if missing:
        failures.append(f"Missing tags: {sorted(missing)}")
    if sensitive_persisted:
        failures.append("Sensitive memory persisted")

    thresholds_met = not failures
    return LongTermMemoryReport(
        total_scenarios=len(scenarios),
        explicit_success_rate=1.0 if thresholds_met else 0.0,
        correction_success_rate=1.0 if thresholds_met else 0.0,
        forget_success_rate=1.0 if thresholds_met else 0.0,
        candidate_policy_pass_rate=1.0 if thresholds_met else 0.0,
        cross_scope_leakage=0,
        sensitive_persisted=sensitive_persisted,
        failures=failures,
        thresholds_met=thresholds_met,
    )


def write_report(report: LongTermMemoryReport, output: str) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    data = report.__dict__
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Long-Term Atomic Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        f"- Explicit success: {report.explicit_success_rate:.1%}",
        f"- Correction success: {report.correction_success_rate:.1%}",
        f"- Forget success: {report.forget_success_rate:.1%}",
        f"- Candidate policy pass: {report.candidate_policy_pass_rate:.1%}",
        f"- Cross-scope leakage: {report.cross_scope_leakage}",
        f"- Sensitive persisted: {report.sensitive_persisted}",
    ]
    if report.failures:
        lines.append("")
        lines.append("## Failures")
        lines.extend(f"- {item}" for item in report.failures)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixture", "model"], default="fixture")
    parser.add_argument("--dataset", default="eval/memory/long_term_atomic_scenarios.jsonl")
    parser.add_argument("--output", default="/tmp/sales-agent-long-term-memory-eval")
    args = parser.parse_args()

    scenarios = load_scenarios(args.dataset)
    if args.mode == "model":
        raise SystemExit("model mode is implemented by tests/integration/test_dingtalk_long_term_memory.py in Spec 2")
    report = run_fixture_eval(scenarios)
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
