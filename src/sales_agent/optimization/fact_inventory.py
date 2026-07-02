"""Fact inventory: extract, deduplicate, and track conflicts across versions.

Facts are stored per knowledge_version_id and document_revision_id.
Deduplication uses a canonical hash computed from sorted normalized fields.
Conflicting facts produce review items and cannot seed factual questions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.knowledge_fact import KnowledgeFact
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


@dataclass
class FactRecord:
    """Input/output record for fact storage."""
    subject: str
    predicate: str
    object_values: list[str] = field(default_factory=list)
    qualifiers: dict[str, Any] = field(default_factory=dict)
    document_revision_id: str = ""
    document_id: str = ""
    evidence_offsets: list[int] = field(default_factory=list)
    effective_start: str | None = None
    effective_end: str | None = None
    extractor_name: str | None = None
    extractor_version: str | None = None

    @property
    def id(self) -> str:
        return ""  # set after persistence


@dataclass
class FactInventoryResult:
    fact_id: str
    canonical_hash: str
    conflict_status: str  # unique / conflicting


class FactInventory:
    """Manage versioned knowledge facts with dedup and conflict detection."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def compute_hash(fact: FactRecord) -> str:
        """Stable hash from sorted normalized fields."""
        payload = {
            "subject": fact.subject.lower().strip(),
            "predicate": fact.predicate.lower().strip(),
            "object_values": sorted(fact.object_values),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    async def store(
        self,
        tenant_id: str,
        knowledge_version_id: str,
        fact: FactRecord,
    ) -> FactInventoryResult:
        """Store one fact, returning existing ID if deduplicated."""
        canonical_hash = self.compute_hash(fact)

        # Check for existing fact with same hash in this tenant
        existing = await self.db.scalar(
            select(KnowledgeFact).where(
                KnowledgeFact.tenant_id == tenant_id,
                KnowledgeFact.canonical_hash == canonical_hash,
            )
        )
        if existing is not None:
            return FactInventoryResult(
                fact_id=existing.id,
                canonical_hash=canonical_hash,
                conflict_status=existing.conflict_status,
            )

        # Check for conflicting effective values (same subject+predicate, different object)
        conflicts = await self._detect_conflicts(tenant_id, knowledge_version_id, fact)
        conflict_status = "conflicting" if conflicts else "unique"

        row = KnowledgeFact(
            id=generate_id(),
            tenant_id=tenant_id,
            knowledge_version_id=knowledge_version_id,
            document_revision_id=fact.document_revision_id,
            document_id=fact.document_id,
            subject=fact.subject,
            predicate=fact.predicate,
            object_json=json.dumps(fact.object_values, ensure_ascii=False),
            qualifiers_json=json.dumps(fact.qualifiers, ensure_ascii=False),
            evidence_offsets_json=json.dumps(fact.evidence_offsets),
            effective_start=fact.effective_start,
            effective_end=fact.effective_end,
            extractor_name=fact.extractor_name,
            extractor_version=fact.extractor_version,
            canonical_hash=canonical_hash,
            conflict_status=conflict_status,
        )
        self.db.add(row)
        await self.db.flush()

        return FactInventoryResult(
            fact_id=row.id,
            canonical_hash=canonical_hash,
            conflict_status=conflict_status,
        )

    async def store_many(
        self,
        tenant_id: str,
        knowledge_version_id: str,
        facts: list[FactRecord],
    ) -> list[FactInventoryResult]:
        """Store multiple facts, detecting conflicts across the batch."""
        results: list[FactInventoryResult] = []
        for fact in facts:
            result = await self.store(tenant_id, knowledge_version_id, fact)
            results.append(result)
        return results

    async def _detect_conflicts(
        self,
        tenant_id: str,
        knowledge_version_id: str,
        fact: FactRecord,
    ) -> bool:
        """Check if another fact with same subject+predicate but different object exists."""
        existing = await self.db.execute(
            select(KnowledgeFact).where(
                KnowledgeFact.tenant_id == tenant_id,
                KnowledgeFact.knowledge_version_id == knowledge_version_id,
                KnowledgeFact.subject == fact.subject,
                KnowledgeFact.predicate == fact.predicate,
            )
        )
        for row in existing.scalars().all():
            existing_obj = json.loads(row.object_json) if row.object_json else []
            if sorted(existing_obj) != sorted(fact.object_values):
                # Mark both as conflicting
                row.conflict_status = "conflicting"
                return True
        return False

    async def get_facts_for_version(
        self, tenant_id: str, knowledge_version_id: str,
    ) -> list[KnowledgeFact]:
        """Return all facts for a knowledge version, excluding conflicts."""
        result = await self.db.execute(
            select(KnowledgeFact).where(
                KnowledgeFact.tenant_id == tenant_id,
                KnowledgeFact.knowledge_version_id == knowledge_version_id,
                KnowledgeFact.conflict_status != "conflicting",
            )
        )
        return list(result.scalars().all())
