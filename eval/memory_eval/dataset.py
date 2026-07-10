"""Scenario dataset loading + anonymization/secret gate (Spec 4 §4, §10)."""
from __future__ import annotations

import json
import re

from eval.memory_eval.schema import MultiturnScenario

# Patterns that must never enter a committed dataset (§10).
_SECRET_PATTERNS = [
    # No \b: CJK characters are \w in Unicode, so \b fails between e.g. 的 and 密码.
    # Over-detection is safe for a security gate; under-detection is not.
    re.compile(r"(?i)(password|passwd|密码|secret|api[_-]?key|token)"),
]
# Direct identifiers — phone numbers, 15/18-digit Chinese ID cards, email.
_PHONE = re.compile(r"\b1[3-9]\d{9}\b")
_IDCARD = re.compile(r"\b(?:\d{15}|\d{17}[\dXx])\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

_UNREVIEWED_TAG = "unreviewed_production"


class DatasetValidationError(Exception):
    """Raised when a dataset cannot be loaded (bad JSON / schema)."""


def load_scenarios(path: str) -> list[MultiturnScenario]:
    scenarios: list[MultiturnScenario] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                scenarios.append(MultiturnScenario(**obj))
            except Exception as exc:  # noqa: BLE001
                raise DatasetValidationError(f"{path}:{lineno}: {exc}") from exc
    return scenarios


def _scan_text(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            hits.append("raw secret/credential")
    if _PHONE.search(text):
        hits.append("direct identifier (phone)")
    if _IDCARD.search(text):
        hits.append("direct identifier (id card)")
    if _EMAIL.search(text):
        hits.append("direct identifier (email)")
    return hits


def validate_dataset(scenarios: list[MultiturnScenario]) -> list[str]:
    """Return a list of human-readable errors; empty list means valid (§10)."""
    errors: list[str] = []
    seen: set[str] = set()
    for s in scenarios:
        if s.id in seen:
            errors.append(f"Duplicate scenario ID: {s.id}")
        seen.add(s.id)
        if _UNREVIEWED_TAG in s.tags:
            errors.append(f"Scenario {s.id}: unreviewed production data tag present")
        for i, turn in enumerate(s.turns):
            for hit in _scan_text(turn.input):
                errors.append(f"Scenario {s.id} turn {i}: {hit} in input")
            for hit in _scan_text(" ".join(turn.expected.reply_contains)):
                errors.append(f"Scenario {s.id} turn {i}: {hit} in reply_contains")
    return errors


__all__ = ["DatasetValidationError", "load_scenarios", "validate_dataset"]
