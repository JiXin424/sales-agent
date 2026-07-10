"""Sales action cards - deterministic fixture eval + release gate runner.

Two modes:

- ``fixture`` (default, deterministic): validates the JSONL scenario dataset's
  internal consistency -- required tags/fields, clear-create vs clarification
  routing expectations, no-duplicate-send idempotency expectations, and the
  "tasks must not pollute long-term memory / profile recall" expectation. This
  is a dataset validator, NOT a model run: it asserts the scenarios encode the
  spec's invariants so the gate can run without an LLM provider.

- ``model`` / ``live``: NOT implemented. Live behavior is covered by the
  integration tests (``tests/integration/test_dingtalk_sales_actions.py`` etc.)
  and the deepeval conversation harness. The CLI stub points there and refuses
  to fabricate metrics.

Usage::

    python3 eval/run_sales_action_eval.py --mode fixture \\
        --dataset eval/sales_actions/action_scenarios.jsonl \\
        --output /tmp/sales-action-eval
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Scenario tags that encode a clear (explicit or confidently-defaulted) create.
CLEAR_CREATE_TAGS = frozenset({"clear_create", "defaulted_time"})
# Scenario tags that encode an incomplete-time / incomplete-action clarification.
CLARIFICATION_TAGS = frozenset({"fuzzy_clarify", "missing_action"})

# Every scenario's ``expects`` must carry these keys (dataset schema).
_REQUIRED_EXPECTS = ("action", "creates_task", "duplicate_send", "writes_memory")


@dataclass
class FixtureReport:
    """Result of the deterministic fixture eval over the scenario dataset."""

    total_scenarios: int = 0
    schema_violations: int = 0
    clear_create_accuracy: float = 0.0
    clarification_accuracy: float = 0.0
    duplicate_send_violations: int = 0
    memory_pollution_violations: int = 0
    thresholds_met: bool = False
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_scenarios": self.total_scenarios,
            "schema_violations": self.schema_violations,
            "clear_create_accuracy": self.clear_create_accuracy,
            "clarification_accuracy": self.clarification_accuracy,
            "duplicate_send_violations": self.duplicate_send_violations,
            "memory_pollution_violations": self.memory_pollution_violations,
            "thresholds_met": self.thresholds_met,
            "violations": self.violations,
        }


def load_scenarios(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load JSONL scenarios (one JSON object per line; blank lines skipped)."""
    scenarios: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            scenarios.append(obj)
    return scenarios


def _expect(scenario: dict[str, Any], key: str, default: Any = None) -> Any:
    expects = scenario.get("expects") or {}
    return expects.get(key, default)


def run_fixture_eval(scenarios: list[dict[str, Any]]) -> FixtureReport:
    """Validate the dataset encodes the spec's sales-action invariants.

    Deterministic: no LLM, no DB. Asserts the scenarios are self-consistent so
    the release gate can run anywhere. Returns a :class:`FixtureReport`.
    """
    report = FixtureReport(total_scenarios=len(scenarios))

    # --- schema: every scenario needs id/tag/input/expects(+required keys) ---
    for sc in scenarios:
        sid = sc.get("id", "<no-id>")
        for key in ("id", "tag", "input", "expects"):
            if key not in sc or sc[key] in (None, ""):
                report.schema_violations += 1
                report.violations.append(f"{sid}: missing '{key}'")
        expects = sc.get("expects") or {}
        if not isinstance(expects, dict):
            report.schema_violations += 1
            report.violations.append(f"{sid}: 'expects' is not an object")
            continue
        for key in _REQUIRED_EXPECTS:
            if key not in expects:
                report.schema_violations += 1
                report.violations.append(f"{sid}: missing 'expects.{key}'")

    # --- clear-create routing: explicit/defaulted create -> action=create, task created ---
    clear_create = [s for s in scenarios if s.get("tag") in CLEAR_CREATE_TAGS]
    if clear_create:
        ok = sum(
            1
            for s in clear_create
            if _expect(s, "action") == "create" and _expect(s, "creates_task") is True
        )
        report.clear_create_accuracy = ok / len(clear_create)
        for s in clear_create:
            if not (_expect(s, "action") == "create" and _expect(s, "creates_task") is True):
                report.violations.append(f"{s.get('id')}: clear-create did not expect create+task")

    # --- clarification routing: fuzzy/missing -> action=clarify, no task created ---
    clarifications = [s for s in scenarios if s.get("tag") in CLARIFICATION_TAGS]
    if clarifications:
        ok = sum(
            1
            for s in clarifications
            if _expect(s, "action") == "clarify" and _expect(s, "creates_task") is False
        )
        report.clarification_accuracy = ok / len(clarifications)
        for s in clarifications:
            if not (_expect(s, "action") == "clarify" and _expect(s, "creates_task") is False):
                report.violations.append(f"{s.get('id')}: clarification did not expect clarify+no-task")

    # --- idempotency: no scenario expects a duplicate send ---
    for s in scenarios:
        if _expect(s, "duplicate_send") is True:
            report.duplicate_send_violations += 1
            report.violations.append(f"{s.get('id')}: expects duplicate_send (idempotency violation)")

    # --- memory pollution: no scenario expects task data to enter long-term memory ---
    for s in scenarios:
        if _expect(s, "writes_memory") is True:
            report.memory_pollution_violations += 1
            report.violations.append(f"{s.get('id')}: expects writes_memory (pollution violation)")

    report.thresholds_met = (
        report.total_scenarios >= 12
        and report.schema_violations == 0
        and report.clear_create_accuracy == 1.0
        and report.clarification_accuracy == 1.0
        and report.duplicate_send_violations == 0
        and report.memory_pollution_violations == 0
    )
    return report


def _write_output(report: FixtureReport, output_dir: str | os.PathLike[str]) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fixture_report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out / "fixture_report.json"


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sales action fixture eval / release gate runner")
    parser.add_argument("--mode", default="fixture", choices=["fixture", "model", "live"])
    parser.add_argument("--dataset", default="eval/sales_actions/action_scenarios.jsonl")
    parser.add_argument("--output", default="/tmp/sales-agent-sales-action-gate")
    args = parser.parse_args(argv)

    if args.mode in ("model", "live"):
        # Not implemented. Live behavior is covered by the integration suite +
        # deepeval conversation harness; do NOT fabricate model-mode metrics.
        print(
            "model/live mode is not implemented. Run the integration tests instead:\n"
            "  PYTHONPATH=src pytest -q tests/integration/test_dingtalk_sales_actions.py "
            "tests/integration/test_sales_action_scheduler.py tests/integration/test_sales_action_callbacks.py\n"
            "For conversation-level deepeval, see eval/deepeval_conversation_eval.py.",
            file=sys.stderr,
        )
        return 2

    scenarios = load_scenarios(args.dataset)
    report = run_fixture_eval(scenarios)
    path = _write_output(report, args.output)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"\nreport written to {path}", file=sys.stderr)
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
