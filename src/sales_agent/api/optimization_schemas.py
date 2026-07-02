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
