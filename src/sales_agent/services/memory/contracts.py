from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator

MemoryType = Literal[
    "user_fact",
    "response_preference",
    "coaching_goal",
    "sales_pattern",
    "recurring_challenge",
]
MemoryStatus = Literal["candidate", "active", "superseded", "deleted", "expired", "rejected"]
SourceKind = Literal["explicit_user", "inferred_user", "verified_tool"]
Sensitivity = Literal["normal", "restricted", "prohibited"]
ConfidenceBand = Literal["confirmed", "corroborated", "candidate"]


class MemoryScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    user_id: str
    subject_type: Literal["user"] = "user"

    @property
    def subject_id(self) -> str:
        return self.user_id

    @field_validator("tenant_id", "agent_id", "user_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope identifiers must not be blank")
        return value


class MemoryCandidate(BaseModel):
    memory_type: MemoryType
    normalized_key: str
    content: dict[str, Any]
    evidence_text: str
    source_kind: SourceKind
    stability: Literal["stable", "temporary", "unclear"]
    sensitivity: Sensitivity
    confidence_band: ConfidenceBand

    @field_validator("normalized_key", "evidence_text")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required text field is blank")
        return value.strip()


class AtomicMemoryRecord(BaseModel):
    id: str
    scope: MemoryScope
    memory_type: MemoryType
    normalized_key: str
    content: dict[str, Any]
    search_text: str
    status: MemoryStatus
    source_kind: SourceKind
    source_conversation_id: str
    source_message_ids: list[str] = Field(default_factory=list)
    evidence_count: int
    confidence_band: ConfidenceBand
    sensitivity: Sensitivity
    supersedes_id: str | None = None
    observed_at: datetime
    last_confirmed_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryOperationResult(BaseModel):
    operation: Literal["remember", "correct", "forget", "candidate", "expire", "noop"]
    status: Literal["success", "rejected", "clarify", "failed", "noop"]
    response_text: str
    memory_ids: list[str] = Field(default_factory=list)
    reason_code: str
    candidate_count: int = 0


@dataclass(frozen=True)
class MemoryWriteDecision:
    action: Literal["activate", "candidate", "reject", "clarify"]
    status: MemoryStatus
    reason_code: str
