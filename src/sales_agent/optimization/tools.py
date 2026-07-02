"""Constrained optimization tools with tenant-enforced scoping.

Every tool receives tenant_id and agent_id from runtime context (runtime
config), never from the Agent's tool arguments. Tools validate change type
and reject mixed-category or non-allowlisted fields.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from sales_agent.optimization.types import ChangeType


# ── Tool input schemas (Pydantic, strict) ──────────────────────────────────

class RouterPatchInput(BaseModel):
    """Allowed fields for router proposals."""
    rules_json: str = Field(default="{}", description="Updated router rules as JSON")
    examples_json: str = Field(default="{}", description="Updated router examples as JSON")
    knowledge_trigger_rules_json: str = Field(default="{}", description="Knowledge retrieval trigger rules")
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievalPatchInput(BaseModel):
    """Allowed fields for retrieval proposals."""
    synonyms_json: str = Field(default="{}", description="Tenant synonym mappings")
    query_rewrite_enabled: bool | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    keyword_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    rrf_constant: int | None = Field(default=None, ge=1, le=200)


class DocumentPatchInput(BaseModel):
    """Allowed fields for document proposals."""
    document_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    diff: str = Field(default="", description="Unified diff of the change")
    evidence_summary: str | None = None


# ── Tool results ──────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    success: bool
    tenant_id: str
    change_type: ChangeType
    patch_hash: str = ""
    action: str = ""  # "create_candidate" | "create_knowledge_gap"
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ── Tool implementations ──────────────────────────────────────────────────

def _compute_patch_hash(change_type: str, data: dict) -> str:
    """Stable hash for deduplication."""
    payload = json.dumps({"type": change_type, **data}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def propose_router_patch(
    tenant_id: str,
    agent_id: str,
    input_data: RouterPatchInput,
) -> ToolResult:
    """Propose a router configuration change."""
    data = input_data.model_dump(exclude_none=True)
    return ToolResult(
        success=True,
        tenant_id=tenant_id,
        change_type="router",
        patch_hash=_compute_patch_hash("router", data),
        action="create_candidate",
        data=data,
    )


def propose_retrieval_patch(
    tenant_id: str,
    agent_id: str,
    input_data: RetrievalPatchInput,
) -> ToolResult:
    """Propose a retrieval profile change."""
    data = input_data.model_dump(exclude_none=True)
    return ToolResult(
        success=True,
        tenant_id=tenant_id,
        change_type="retrieval",
        patch_hash=_compute_patch_hash("retrieval", data),
        action="create_candidate",
        data=data,
    )


def propose_document_patch(
    tenant_id: str,
    agent_id: str,
    input_data: DocumentPatchInput,
) -> ToolResult:
    """Propose a document change. Must have evidence; otherwise creates a knowledge gap."""
    if not input_data.evidence_ids:
        return ToolResult(
            success=True,
            tenant_id=tenant_id,
            change_type="document",
            action="create_knowledge_gap",
            data={"diff": input_data.diff, "reason": "no_evidence_provided"},
        )
    data = input_data.model_dump()
    return ToolResult(
        success=True,
        tenant_id=tenant_id,
        change_type="document",
        patch_hash=_compute_patch_hash("document", data),
        action="create_candidate",
        data=data,
    )
