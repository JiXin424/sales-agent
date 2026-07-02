"""Optimization domain types: diagnosis, candidates, and attribution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Diagnosis cause enumeration ─────────────────────────────────────────────

CauseType = Literal[
    "invalid_eval_case",
    "route_miss",
    "document_missing",
    "document_wrong",
    "document_conflict",
    "retrieval_recall",
    "retrieval_ranking",
    "context_noise",
    "chunking_or_structure",
    "generation_issue",
]

RecommendedAction = Literal[
    "fix_eval_case",
    "update_router",
    "add_document",
    "fix_document",
    "resolve_conflict",
    "update_retrieval_profile",
    "improve_chunking",
    "improve_generation",
    "human_review",
]

ChangeType = Literal["router", "retrieval", "document"]

ALL_CAUSE_TYPES: tuple[CauseType, ...] = (
    "invalid_eval_case",
    "route_miss",
    "document_missing",
    "document_wrong",
    "document_conflict",
    "retrieval_recall",
    "retrieval_ranking",
    "context_noise",
    "chunking_or_structure",
    "generation_issue",
)

# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class FailureDiagnosis:
    """Deterministic attribution result for a cluster of failed cases."""

    primary_cause: CauseType
    secondary_causes: list[CauseType] = field(default_factory=list)
    confidence: float = 0.0  # 0.0–1.0
    evidence: list[str] = field(default_factory=list)
    blocked_checks: list[str] = field(default_factory=list)
    recommended_action: RecommendedAction = "human_review"
    affected_case_ids: list[str] = field(default_factory=list)


@dataclass
class OracleLookupResult:
    """Result of an oracle corpus lookup for a required fact."""

    status: Literal["present", "absent", "conflicting", "invalid_lineage"]
    supporting_chunks: list[str] = field(default_factory=list)
    conflicting_chunks: list[str] = field(default_factory=list)
    evidence_summary: str = ""


@dataclass
class CandidatePatch:
    """A proposed optimization candidate patch."""

    change_type: ChangeType
    hypothesis: str
    diagnosis_id: str
    attempt_number: int = 1
    # Router patch fields
    router_rules_json: str | None = None
    router_examples_json: str | None = None
    # Retrieval patch fields
    synonyms_json: str | None = None
    query_rewrite_enabled: bool | None = None
    retrieval_params_json: str | None = None
    # Document patch fields
    document_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    diff: str | None = None
    # Metadata
    changed_variables: list[str] = field(default_factory=list)
    patch_hash: str = ""


@dataclass
class EvalComparison:
    """Baseline-to-candidate evaluation comparison."""

    metric_name: str
    baseline_score: float | None
    candidate_score: float | None
    delta: float | None
    is_regression: bool = False
    is_improvement: bool = False
    judge_unstable: bool = False
