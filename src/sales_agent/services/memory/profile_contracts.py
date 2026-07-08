from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkContext(BaseModel):
    role: str | None = None
    sales_region: str | None = None
    product_focus: list[str] = Field(default_factory=list)


class ResponsePreferences(BaseModel):
    verbosity: str | None = None
    format: list[str] = Field(default_factory=list)
    coaching_style: str | None = None


class DevelopmentProfile(BaseModel):
    coaching_goals: list[str] = Field(default_factory=list)
    recurring_challenges: list[str] = Field(default_factory=list)
    confirmed_sales_patterns: list[str] = Field(default_factory=list)


class UserMemoryProfileDocument(BaseModel):
    work_context: WorkContext = Field(default_factory=WorkContext)
    response_preferences: ResponsePreferences = Field(default_factory=ResponsePreferences)
    development: DevelopmentProfile = Field(default_factory=DevelopmentProfile)


EMPTY_PROFILE: dict[str, Any] = UserMemoryProfileDocument().model_dump()


@dataclass(frozen=True)
class ProfileProjectionResult:
    profile: dict[str, Any]
    evidence_map: dict[str, list[str]]
    source_memory_version: str
    generated_at: datetime


class RecallItem(BaseModel):
    memory_id: str
    memory_type: str
    normalized_key: str
    text: str
    last_confirmed_at: datetime | None = None
    evidence_count: int = 1


class RecallTrace(BaseModel):
    profile_version: int | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    eligible_memory_types: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    candidate_count: int = 0
    retrieval_latency_ms: int = 0
    degraded: bool = False
    degradation_reason: str | None = None


@dataclass(frozen=True)
class RecallResult:
    context_text: str
    selected_items: list[RecallItem]
    trace: RecallTrace
