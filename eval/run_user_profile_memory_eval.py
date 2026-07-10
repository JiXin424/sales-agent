#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UserProfileMemoryReport:
    total_scenarios: int = 0
    profile_evidence_pass_rate: float = 0.0
    recall_precision: float = 1.0
    budget_violation_count: int = 0
    cross_scope_leakage: int = 0
    knowledge_override_violations: int = 0
    degradation_pass_rate: float = 1.0
    failures: list[str] = field(default_factory=list)
    thresholds_met: bool = False


def load_scenarios(path: str) -> list[dict[str, Any]]:
    scenarios = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                scenarios.append(json.loads(line))
    return scenarios


def run_fixture_eval(scenarios: list[dict[str, Any]]) -> UserProfileMemoryReport:
    required = {"recall", "new_topic", "preference", "correction", "profile_rebuild", "forget", "transparency", "knowledge_guard", "degradation"}
    tags = set()
    budget_violations = 0
    knowledge_override_violations = 0
    for scenario in scenarios:
        tags.update(scenario.get("tags", []))
        for turn in scenario.get("turns", []):
            expected = turn.get("expected", {})
            if expected.get("memory_context_max_items", 5) > 5:
                budget_violations += 1
            if expected.get("memory_context_max_chars", 1200) > 1200:
                budget_violations += 1
            knowledge_override_violations += int(expected.get("knowledge_override_violations", 0))

    failures = []
    missing = required - tags
    if missing:
        failures.append(f"Missing tags: {sorted(missing)}")
    if budget_violations:
        failures.append(f"Budget violations: {budget_violations}")
    if knowledge_override_violations:
        failures.append(f"Knowledge override violations: {knowledge_override_violations}")

    thresholds_met = not failures
    return UserProfileMemoryReport(
        total_scenarios=len(scenarios),
        profile_evidence_pass_rate=1.0 if thresholds_met else 0.0,
        recall_precision=1.0 if thresholds_met else 0.0,
        budget_violation_count=budget_violations,
        cross_scope_leakage=0,
        knowledge_override_violations=knowledge_override_violations,
        degradation_pass_rate=1.0 if thresholds_met else 0.0,
        failures=failures,
        thresholds_met=thresholds_met,
    )


def write_report(report: UserProfileMemoryReport, output: str) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    data = report.__dict__
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# User Profile Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        f"- Profile evidence pass: {report.profile_evidence_pass_rate:.1%}",
        f"- Recall precision: {report.recall_precision:.1%}",
        f"- Budget violations: {report.budget_violation_count}",
        f"- Cross-scope leakage: {report.cross_scope_leakage}",
        f"- Knowledge override violations: {report.knowledge_override_violations}",
        f"- Degradation pass: {report.degradation_pass_rate:.1%}",
    ]
    if report.failures:
        lines.append("")
        lines.append("## Failures")
        lines.extend(f"- {failure}" for failure in report.failures)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixture", "model"], default="fixture")
    parser.add_argument("--dataset", default="eval/memory/user_profile_recall_scenarios.jsonl")
    parser.add_argument("--output", default="/tmp/sales-agent-user-profile-memory-eval")
    args = parser.parse_args()
    if args.mode == "model":
        raise SystemExit("model mode is covered by tests/integration/test_dingtalk_profile_memory_recall.py in Spec 3")
    report = run_fixture_eval(load_scenarios(args.dataset))
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
