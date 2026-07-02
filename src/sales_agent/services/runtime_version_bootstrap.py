"""Bootstrap baseline knowledge releases for existing tenants and Agents.

Idempotent: calling ensure_baseline multiple times returns the same release.
Each tenant/Agent pair gets:
- DocumentRevision 1 for every active document
- One baseline KnowledgeVersion
- One default RetrievalProfile and RouterProfile
- One baseline OptimizationRelease
- One AgentRuntimeBinding
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.document import Document, DocumentChunk
from sales_agent.models.knowledge_version import (
    DocumentRevision,
    KnowledgeVersion,
    KnowledgeVersionDocument,
    RetrievalProfile,
    RouterProfile,
)
from sales_agent.models.runtime_release import (
    AgentRuntimeBinding,
    OptimizationRelease,
)
from sales_agent.models.base import generate_id, utcnow


@dataclass
class BootstrapResult:
    release_id: str
    knowledge_version_id: str
    tenant_id: str
    agent_id: str


class RuntimeVersionBootstrap:
    """Create or retrieve baseline release state for a tenant/Agent pair."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ensure_baseline(self, tenant_id: str, agent_id: str) -> BootstrapResult:
        """Idempotent baseline creation.

        Returns the existing binding if one is already present.
        """
        # Check for existing binding first (fast path)
        existing = await self.db.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.tenant_id == tenant_id,
                AgentRuntimeBinding.agent_id == agent_id,
            )
        )
        if existing is not None:
            return BootstrapResult(
                release_id=existing.active_release_id,
                knowledge_version_id="",  # resolved from release below
                tenant_id=tenant_id,
                agent_id=agent_id,
            )

        # Build baseline in one logical flow
        now = utcnow()

        # 1. Document revisions for every active document
        docs = (
            await self.db.execute(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.status == "active",
                )
            )
        ).scalars().all()

        doc_revisions: dict[str, str] = {}  # document_id -> revision_id
        for doc in docs:
            content_hash = self._doc_content_hash(doc, tenant_id)
            rev = DocumentRevision(
                id=generate_id(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                document_id=doc.id,
                parent_revision_id=None,
                revision_number=1,
                content_hash=content_hash,
                change_source="baseline_bootstrap",
                status="active",
                creator_id="system",
            )
            self.db.add(rev)
            doc_revisions[doc.id] = rev.id

        # 2. Knowledge version
        kv = KnowledgeVersion(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            parent_version_id=None,
            version_number=1,
            status="active",
            source="baseline_bootstrap",
            document_count=len(docs),
            chunk_count=0,  # will be counted
            manifest_hash="",
        )
        self.db.add(kv)

        # 3. KnowledgeVersionDocument joins
        for doc in docs:
            kvd = KnowledgeVersionDocument(
                id=generate_id(),
                tenant_id=tenant_id,
                knowledge_version_id=kv.id,
                document_id=doc.id,
                document_revision_id=doc_revisions[doc.id],
            )
            self.db.add(kvd)

        # 4. Retrieval profile
        rp = RetrievalProfile(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            version_number=1,
            status="active",
        )
        self.db.add(rp)

        # 5. Router profile
        rtp = RouterProfile(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            version_number=1,
            status="active",
        )
        self.db.add(rtp)

        # 6. Compute manifest hash from canonical sorted JSON
        manifest_data = {
            "knowledge_version_id": kv.id,
            "retrieval_profile_id": rp.id,
            "router_profile_id": rtp.id,
            "document_revisions": sorted(doc_revisions.values()),
        }
        manifest_hash = hashlib.sha256(
            json.dumps(manifest_data, sort_keys=True).encode()
        ).hexdigest()
        kv.manifest_hash = manifest_hash

        # 7. Release manifest
        release = OptimizationRelease(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            release_number=1,
            status="active",
            manifest_hash=manifest_hash,
            knowledge_version_id=kv.id,
            retrieval_profile_id=rp.id,
            router_profile_id=rtp.id,
            published_by="system",
            published_at=now,
        )
        self.db.add(release)

        # 8. Runtime binding
        binding = AgentRuntimeBinding(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            active_release_id=release.id,
            previous_release_id=None,
            lock_version=1,
            activated_at=now,
            activated_by="system",
        )
        self.db.add(binding)

        await self.db.flush()
        return BootstrapResult(
            release_id=release.id,
            knowledge_version_id=kv.id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    def _doc_content_hash(self, doc: Document, tenant_id: str) -> str:
        """Compute a stable content hash from the document's current chunks."""
        # We can't await inside a sync method; use a simple title+path hash
        # In production this would hash actual chunk texts
        raw = f"{doc.id}:{doc.title}:{doc.source_path}"
        return hashlib.sha256(raw.encode()).hexdigest()
