"""Sandbox version builder: creates isolated knowledge/config versions for candidates.

Candidate chunks NEVER enter the active release — they are built under a
candidate-scoped knowledge_version_id that is only reachable through
explicit candidate evaluation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id
from sales_agent.models.knowledge_version import (
    KnowledgeVersion,
    KnowledgeVersionDocument,
    DocumentRevision,
    RetrievalProfile,
    RouterProfile,
)

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of building a sandbox version for a candidate."""
    knowledge_version_id: str
    retrieval_profile_id: str | None
    router_profile_id: str | None
    document_revision_ids: list[str]


class SandboxBuilder:
    """Builds isolated candidate versions for evaluation.

    The sandbox is a copy-on-write mechanism: it creates new version rows
    that reference the candidate's proposed changes without touching the
    active production versions.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        base_knowledge_version_id: str,
        base_retrieval_profile_id: str,
        base_router_profile_id: str,
        candidate_id: str,
        change_type: str,
        patch_data: dict,
    ) -> SandboxResult:
        """Create sandbox versions for a candidate.

        Args:
            tenant_id: Tenant scope.
            agent_id: Agent scope.
            base_knowledge_version_id: The baseline knowledge version to fork.
            base_retrieval_profile_id: Baseline retrieval profile.
            base_router_profile_id: Baseline router profile.
            candidate_id: The candidate this sandbox belongs to.
            change_type: "router", "retrieval", or "document".
            patch_data: The structured patch payload.

        Returns:
            SandboxResult with the new sandbox version IDs.
        """
        revision_ids: list[str] = []

        # Fork knowledge version
        sandbox_kv = KnowledgeVersion(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            parent_version_id=base_knowledge_version_id,
            version_number=1,  # will be resolved from parent at read time
            status="sandbox",
            source="candidate",
            candidate_id=candidate_id,
        )
        self.db.add(sandbox_kv)

        # Fork retrieval profile (if change is retrieval)
        sandbox_rp_id: str | None = None
        if change_type == "retrieval":
            sandbox_rp = RetrievalProfile(
                id=generate_id(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                parent_profile_id=base_retrieval_profile_id,
                version_number=1,
                status="sandbox",
                candidate_id=candidate_id,
                tenant_synonyms_json=patch_data.get("synonyms_json", "{}"),
                query_rewrite_enabled=patch_data.get("query_rewrite_enabled"),
                top_k=patch_data.get("top_k", 5),
                candidate_k=patch_data.get("candidate_k", 30),
                min_score=patch_data.get("min_score", 0.0),
                keyword_weight=patch_data.get("keyword_weight", 0.3),
                rrf_constant=patch_data.get("rrf_constant", 60),
            )
            self.db.add(sandbox_rp)
            sandbox_rp_id = sandbox_rp.id

        # Fork router profile (if change is router)
        sandbox_rtp_id: str | None = None
        if change_type == "router":
            sandbox_rtp = RouterProfile(
                id=generate_id(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                parent_profile_id=base_router_profile_id,
                version_number=1,
                status="sandbox",
                candidate_id=candidate_id,
                rules_json=patch_data.get("rules_json", "{}"),
                knowledge_trigger_rules_json=patch_data.get("knowledge_trigger_rules_json", "{}"),
                confidence_threshold=patch_data.get("confidence_threshold", 0.6),
            )
            self.db.add(sandbox_rtp)
            sandbox_rtp_id = sandbox_rtp.id

        # Document revisions (if change is document)
        if change_type == "document" and patch_data.get("document_id"):
            doc_rev = DocumentRevision(
                id=generate_id(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                document_id=patch_data["document_id"],
                parent_revision_id=None,  # resolved at build time
                revision_number=2,  # baseline is 1
                content_hash=hashlib.sha256(
                    json.dumps(patch_data, sort_keys=True).encode()
                ).hexdigest(),
                change_source="candidate",
                candidate_id=candidate_id,
                evidence_summary=patch_data.get("evidence_summary"),
                status="sandbox",
                creator_id="system",
            )
            self.db.add(doc_rev)
            revision_ids.append(doc_rev.id)

        await self.db.flush()
        logger.info(
            "Sandbox built: kv=%s rp=%s rtp=%s revs=%d for candidate=%s",
            sandbox_kv.id, sandbox_rp_id, sandbox_rtp_id, len(revision_ids), candidate_id,
        )

        return SandboxResult(
            knowledge_version_id=sandbox_kv.id,
            retrieval_profile_id=sandbox_rp_id,
            router_profile_id=sandbox_rtp_id,
            document_revision_ids=revision_ids,
        )
