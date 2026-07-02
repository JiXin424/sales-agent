"""Test ReleaseService: tenant isolation, optimistic locking, and manifest resolution."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.runtime_release import AgentRuntimeBinding, OptimizationRelease
from sales_agent.models.base import generate_id, utcnow


@pytest_asyncio.fixture
async def binding(db_session: AsyncSession, active_agent):
    """Create a baseline AgentRuntimeBinding for the active agent."""
    from sales_agent.services.runtime_version_bootstrap import (
        RuntimeVersionBootstrap,
    )
    from sqlalchemy import select

    svc = RuntimeVersionBootstrap(db_session)
    await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)

    # Fetch the actual binding from DB to get current state
    binding = await db_session.scalar(
        select(AgentRuntimeBinding).where(
            AgentRuntimeBinding.tenant_id == active_agent.tenant_id,
            AgentRuntimeBinding.agent_id == active_agent.id,
        )
    )
    return binding


@pytest_asyncio.fixture
async def candidate_release(db_session: AsyncSession, active_agent, binding):
    """Create a second release to use as a candidate for activation."""
    release = OptimizationRelease(
        id=generate_id(),
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        release_number=2,
        status="draft",
        manifest_hash="abc123_candidate",
        knowledge_version_id="kv_test",
        retrieval_profile_id="rp_test",
        router_profile_id="rtp_test",
    )
    db_session.add(release)
    await db_session.flush()
    return release


@pytest_asyncio.fixture
async def release_other_tenant(db_session: AsyncSession, other_agent):
    """Create a release for the other tenant."""
    release = OptimizationRelease(
        id=generate_id(),
        tenant_id=other_agent.tenant_id,
        agent_id=other_agent.id,
        release_number=1,
        status="active",
        manifest_hash="other_tenant_hash",
        knowledge_version_id="kv_other",
        retrieval_profile_id="rp_other",
        router_profile_id="rtp_other",
    )
    db_session.add(release)
    await db_session.flush()
    return release


class TestReleaseService:
    @pytest.mark.asyncio
    async def test_activate_rejects_stale_lock(self, db_session, binding, candidate_release):
        """Activation with wrong lock_version must raise StaleRuntimeBinding."""
        from sales_agent.services.release_service import ReleaseService
        from sales_agent.services.release_types import StaleRuntimeBinding

        service = ReleaseService(db_session)
        with pytest.raises(StaleRuntimeBinding):
            await service.activate(
                tenant_id=binding.tenant_id,
                agent_id=binding.agent_id,
                release_id=candidate_release.id,
                expected_lock_version=binding.lock_version - 1,
                actor_id="reviewer",
            )

    @pytest.mark.asyncio
    async def test_resolve_never_crosses_tenant(self, db_session, release_other_tenant):
        """get_manifest for wrong tenant must raise ReleaseNotFound."""
        from sales_agent.services.release_service import ReleaseService
        from sales_agent.services.release_types import ReleaseNotFound

        with pytest.raises(ReleaseNotFound):
            await ReleaseService(db_session).get_manifest("tenant-a", release_other_tenant.id)

    @pytest.mark.asyncio
    async def test_activate_switches_binding_atomically(self, db_session, binding, candidate_release):
        """Successful activation updates the binding, increments lock, and records event."""
        from sales_agent.services.release_service import ReleaseService
        from sales_agent.models.runtime_release import ReleaseEvent
        from sqlalchemy import select, func

        # Record pre-activation state directly from the binding loaded by service
        service = ReleaseService(db_session)
        pre_binding = await db_session.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.tenant_id == binding.tenant_id,
                AgentRuntimeBinding.agent_id == binding.agent_id,
            )
        )
        prev_active = pre_binding.active_release_id
        prev_lock = pre_binding.lock_version

        await service.activate(
            tenant_id=binding.tenant_id,
            agent_id=binding.agent_id,
            release_id=candidate_release.id,
            expected_lock_version=prev_lock,
            actor_id="reviewer",
        )

        # Re-fetch binding
        updated = await db_session.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.tenant_id == binding.tenant_id,
                AgentRuntimeBinding.agent_id == binding.agent_id,
            )
        )
        assert updated.active_release_id == candidate_release.id
        assert updated.previous_release_id == prev_active
        assert updated.lock_version == prev_lock + 1
        assert updated.activated_by == "reviewer"
        assert updated.activated_at is not None

        # A ReleaseEvent must be recorded
        event_count = await db_session.scalar(
            select(func.count()).select_from(ReleaseEvent).where(
                ReleaseEvent.tenant_id == binding.tenant_id,
                ReleaseEvent.release_id == candidate_release.id,
            )
        )
        assert event_count >= 1

    @pytest.mark.asyncio
    async def test_get_manifest_returns_release(self, db_session, binding):
        """get_manifest returns the release for the correct tenant."""
        from sales_agent.services.release_service import ReleaseService

        service = ReleaseService(db_session)
        manifest = await service.get_manifest(binding.tenant_id, binding.active_release_id)
        assert manifest is not None
        assert manifest.id == binding.active_release_id
        assert manifest.tenant_id == binding.tenant_id
