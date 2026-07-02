"""Question quality checks: semantic dedup, per-document cap, quarantine rules.

Quality-gated questions are flagged for review; quarantined questions are
excluded from automated evaluation until promoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_PER_DOCUMENT = 5  # Maximum questions per document
MAX_PER_FACT = 3      # Maximum questions per fact


@dataclass
class QualityReport:
    """Quality assessment for a generated question suite."""
    total: int = 0
    accepted: int = 0
    quarantined: int = 0
    duplicate_count: int = 0
    over_cap_count: int = 0
    issues: list[str] = field(default_factory=list)


def assess_quality(questions: list[dict]) -> QualityReport:
    """Run quality checks on a list of generated question dicts.

    Checks:
    - Duplicate normalized inputs (semantic dedup)
    - Per-document cap (MAX_PER_DOCUMENT)
    - Per-fact cap (MAX_PER_FACT)
    """
    report = QualityReport(total=len(questions))
    seen_inputs: set[str] = set()
    doc_counts: dict[str, int] = {}
    fact_counts: dict[str, int] = {}

    for q in questions:
        norm = q.get("input_text", "").lower().strip()
        doc_ids = q.get("source_document_ids", [])
        fact_ids = q.get("source_fact_ids", [])

        issues: list[str] = []

        # Semantic dedup
        if norm in seen_inputs:
            issues.append("duplicate_input")
            report.duplicate_count += 1
        seen_inputs.add(norm)

        # Per-document cap
        for doc_id in doc_ids:
            cnt = doc_counts.get(doc_id, 0) + 1
            doc_counts[doc_id] = cnt
            if cnt > MAX_PER_DOCUMENT:
                issues.append(f"over_document_cap:{doc_id}")

        # Per-fact cap
        for fact_id in fact_ids:
            cnt = fact_counts.get(fact_id, 0) + 1
            fact_counts[fact_id] = cnt
            if cnt > MAX_PER_FACT:
                issues.append(f"over_fact_cap:{fact_id}")

        if issues:
            q["quality_status"] = "quarantined"
            report.quarantined += 1
        else:
            q["quality_status"] = "accepted"
            report.accepted += 1

        report.issues.extend(issues)

    return report
