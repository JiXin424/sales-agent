"""Release service: resolve manifests and atomically switch runtime bindings.

Tenant isolation is enforced on every operation.
Optimistic locking prevents concurrent activation races.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.runtime_release import (
    AgentRuntimeBinding,
    OptimizationRelease,
    ReleaseEvent,
)
from sales_agent.models.base import generate_id, utcnow
from sales_agent.services.release_types import (
    ActivateResult,
    ReleaseManifest,
    ReleaseNotFound,
    ReleaseNotDraft,
    StaleRuntimeBinding,
)


class ReleaseService:
    """Resolve immutable release manifests and switch runtime bindings."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_manifest(self, tenant_id: str, release_id: str) -> ReleaseManifest:
        """Resolve a release manifest scoped to tenant.

        Raises ReleaseNotFound if the release does not exist or belongs to
        another tenant.
        """
        release = await self.db.scalar(
            select(OptimizationRelease).where(
                OptimizationRelease.id == release_id,
                OptimizationRelease.tenant_id == tenant_id,
            )
        )
        if release is None:
            raise ReleaseNotFound(
                f"Release {release_id} not found for tenant {tenant_id}"
            )
        return ReleaseManifest(
            id=release.id,
            tenant_id=release.tenant_id,
            agent_id=release.agent_id,
            release_number=release.release_number,
            knowledge_version_id=release.knowledge_version_id,
            retrieval_profile_id=release.retrieval_profile_id,
            router_profile_id=release.router_profile_id,
            manifest_hash=release.manifest_hash,
            prompt_set_id=release.prompt_set_id,
            model_snapshot_json=release.model_snapshot_json,
            graph_definition_version=release.graph_definition_version,
            code_revision=release.code_revision,
        )

    async def activate(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        release_id: str,
        expected_lock_version: int,
        actor_id: str,
    ) -> ActivateResult:
        """Atomically switch the runtime binding to a new release.

        1. Validates the release exists and is scoped to the tenant.
        2. Uses optimistic locking: WHERE lock_version = expected.
        3. Increments lock_version, preserves previous_release_id.
        4. Appends a ReleaseEvent in the same transaction.

        Raises:
            ReleaseNotFound: release does not exist for this tenant.
            StaleRuntimeBinding: lock_version mismatch.
            ReleaseNotDraft: release status is not 'draft'.
        """
        # Validate release exists and is tenant-scoped
        release = await self.db.scalar(
            select(OptimizationRelease).where(
                OptimizationRelease.id == release_id,
                OptimizationRelease.tenant_id == tenant_id,
            )
        )
        if release is None:
            raise ReleaseNotFound(
                f"Release {release_id} not found for tenant {tenant_id}"
            )

        if release.status != "draft":
            raise ReleaseNotDraft(
                f"Release {release_id} status is {release.status}, expected draft"
            )

        # Optimistic lock update
        binding = await self.db.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.tenant_id == tenant_id,
                AgentRuntimeBinding.agent_id == agent_id,
            )
        )
        if binding is None:
            raise ReleaseNotFound(
                f"No runtime binding for tenant {tenant_id}, agent {agent_id}"
            )

        if binding.lock_version != expected_lock_version:
            raise StaleRuntimeBinding(
                f"Expected lock_version {expected_lock_version}, "
                f"got {binding.lock_version}"
            )

        now = utcnow()
        previous_release_id = binding.active_release_id

        # Atomically update binding
        result = await self.db.execute(
            update(AgentRuntimeBinding)
            .where(
                AgentRuntimeBinding.tenant_id == tenant_id,
                AgentRuntimeBinding.agent_id == agent_id,
                AgentRuntimeBinding.lock_version == expected_lock_version,
            )
            .values(
                active_release_id=release_id,
                previous_release_id=previous_release_id,
                lock_version=expected_lock_version + 1,
                activated_at=now,
                activated_by=actor_id,
            )
        )
        if result.rowcount == 0:
            # Race condition: someone else updated between our read and write
            raise StaleRuntimeBinding(
                f"Concurrent modification detected for {tenant_id}/{agent_id}"
            )

        # Update release status
        release.status = "active"
        release.published_by = actor_id
        release.published_at = now

        # Append audit event
        event = ReleaseEvent(
            id=generate_id(),
            tenant_id=tenant_id,
            release_id=release_id,
            actor_id=actor_id,
            event_type="activation",
            status_transition="draft->active",
            payload_json="{}",
        )
        self.db.add(event)

        await self.db.flush()
        return ActivateResult(
            binding_id=binding.id,
            release_id=release_id,
            previous_release_id=previous_release_id,
            new_lock_version=expected_lock_version + 1,
        )
