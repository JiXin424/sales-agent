"""Request/response schemas for optimization API."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class StartIterationRequest(BaseModel):
    fixed_suite_id: str = Field(..., description="Fixed regression suite ID")
    exploration_suite_id: str | None = Field(None)
    max_candidates: int = Field(default=3, ge=1, le=10)
    max_consecutive_failures: int = Field(default=2, ge=0, le=5)
    allowed_change_types: list[str] = Field(default=["router", "retrieval", "document"])


class IterationResponse(BaseModel):
    id: str
    tenant_id: str
    agent_id: str
    iteration_no: int
    status: str
    baseline_release_id: str | None = None
    created_at: str | None = None


class DiagnosisResponse(BaseModel):
    id: str
    primary_cause: str
    confidence: float
    recommended_action: str
    cluster_key: str
    affected_case_ids: list[str]


class CandidateResponse(BaseModel):
    id: str
    change_type: str
    status: str
    attempt_number: int
    hypothesis: str | None = None
    patch_hash: str | None = None


class ApproveRequest(BaseModel):
    actor_id: str = Field(default="operator")


class RejectRequest(BaseModel):
    actor_id: str = Field(default="operator")
    reason: str = Field(default="")


class PublishRequest(BaseModel):
    actor_id: str = Field(default="operator")


class RollbackRequest(BaseModel):
    target_release_id: str
    actor_id: str = Field(default="operator")


class CheckpointForkRequest(BaseModel):
    candidate_id: str


class ReleaseCompareResponse(BaseModel):
    release_id: str
    previous_release_id: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)


class EvalComparisonResponse(BaseModel):
    metric_name: str
    baseline_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None
    is_regression: bool = False


# ── Event schemas ────────────────────────────────────────────────────────────


class EventResponse(BaseModel):
    id: str
    sequence_no: int
    event_type: str
    stage: str | None = None
    status: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    message: str = ""
    payload: Any = Field(default_factory=dict)
    actor_type: str = "system"
    actor_id: str | None = None
    created_at: str | None = None


class EventPageResponse(BaseModel):
    events: list[EventResponse]
    next_sequence: int
    terminal: bool = False


# ── Report schemas ───────────────────────────────────────────────────────────


class ReportMetricResponse(BaseModel):
    metric_name: str
    group_name: str
    direction: str
    weight: float = 0.0
    before_value: float | None = None
    after_value: float | None = None
    before_normalized: float | None = None
    after_normalized: float | None = None
    delta: float | None = None
    applicable: bool = True
    gate_result: str | None = None


class ReportCaseResponse(BaseModel):
    case_id: str
    classification: str
    cause: str | None = None
    before_pass: bool | None = None
    after_pass: bool | None = None
    score_delta: float | None = None
    rank_delta: int | None = None
    latency_delta_ms: float | None = None
    token_delta: int | None = None


class ReportSummaryResponse(BaseModel):
    id: str
    tenant_id: str
    agent_id: str
    iteration_id: str
    report_type: str
    candidate_id: str | None = None
    candidate_key: str
    release_id: str | None = None
    report_version: int
    formula_version: str
    status: str
    recommendation: str | None = None
    effect_index_before: float | None = None
    effect_index_after: float | None = None
    effect_index_delta: float | None = None
    hard_gates: Any = Field(default_factory=dict)
    data_snapshot_hash: str | None = None
    created_at: str | None = None


class ReportDetailResponse(ReportSummaryResponse):
    groups: list[Any] = Field(default_factory=list)
    cases: list[ReportCaseResponse] = Field(default_factory=list)


class TrendResponse(BaseModel):
    agent_id: str
    trends: list[Any] = Field(default_factory=list)
