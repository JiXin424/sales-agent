"""Oracle corpus lookup: searches the full pinned knowledge version for a fact.

Uses only (tenant_id, knowledge_version_id) — never the production retriever.
Determines presence, absence, conflict, or invalid lineage of required facts.
"""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.document import DocumentChunk, Document
from sales_agent.optimization.types import OracleLookupResult


class CorpusOracle:
    """Full-corpus fact lookup scoped to a pinned knowledge version."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lookup_fact(
        self,
        *,
        tenant_id: str,
        knowledge_version_id: str,
        fact_text: str,
        required_entity: str | None = None,
        source_document_ids: list[str] | None = None,
    ) -> OracleLookupResult:
        """Check whether a fact is present in the pinned knowledge version.

        Args:
            tenant_id: Tenant scope.
            knowledge_version_id: Pinned version (never uses active/default).
            fact_text: The fact to search for (full text or key claim).
            required_entity: Optional entity that must co-occur.
            source_document_ids: Optional document lineage from the test case.

        Returns:
            OracleLookupResult with status and supporting/conflicting chunks.
        """
        # Verify the knowledge version exists for this tenant
        from sales_agent.models.knowledge_version import KnowledgeVersion

        kv = await self.db.scalar(
            select(KnowledgeVersion).where(
                KnowledgeVersion.id == knowledge_version_id,
                KnowledgeVersion.tenant_id == tenant_id,
            )
        )
        if kv is None:
            return OracleLookupResult(status="invalid_lineage")

        # Build base query on document_chunks pinned to knowledge_version_id
        query = select(DocumentChunk).where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.knowledge_version_id == knowledge_version_id,
        )

        # Filter by source document lineage if provided
        if source_document_ids:
            query = query.where(DocumentChunk.document_id.in_(source_document_ids))

        rows = (await self.db.execute(query)).scalars().all()

        supporting: list[str] = []
        conflicting: list[str] = []

        # Simple text-match search (case-insensitive)
        fact_lower = fact_text.lower()
        for chunk in rows:
            chunk_text = (chunk.text or "").lower()
            if fact_lower in chunk_text:
                supporting.append(chunk.id)

        if supporting:
            # Check for conflicts: another chunk contradicts
            # (simplified: if required_entity present check variations)
            if required_entity:
                entity_lower = required_entity.lower()
                for chunk in rows:
                    if chunk.id in supporting:
                        continue
                    chunk_text = (chunk.text or "").lower()
                    if fact_lower in chunk_text or entity_lower in chunk_text:
                        # Different document with similar fact → potential conflict
                        conflicting.append(chunk.id)

            status = "conflicting" if conflicting else "present"
        else:
            status = "absent"

        return OracleLookupResult(
            status=status,
            supporting_chunks=supporting,
            conflicting_chunks=conflicting,
            evidence_summary=(
                f"Found {len(supporting)} supporting, "
                f"{len(conflicting)} conflicting chunks "
                f"in kv={knowledge_version_id}"
            ),
        )
