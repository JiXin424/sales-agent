"""Test RuntimeVersionBootstrap creates exactly one baseline per tenant/Agent."""

import pytest
from sqlalchemy import select, func

from sales_agent.models.runtime_release import AgentRuntimeBinding


pytestmark = pytest.mark.anyio


@pytest.mark.asyncio
async def test_bootstrap_creates_one_binding_per_agent(db_session, active_agent):
    """Two calls to ensure_baseline for the same Agent must return the same release."""
    from sales_agent.services.runtime_version_bootstrap import RuntimeVersionBootstrap

    svc = RuntimeVersionBootstrap(db_session)
    first = await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)
    second = await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)

    assert first.release_id == second.release_id

    count = await db_session.scalar(
        select(func.count()).select_from(AgentRuntimeBinding)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_bootstrap_scopes_releases_to_tenant(db_session, active_agent, other_agent):
    """Two tenants must receive separate baseline releases."""
    from sales_agent.services.runtime_version_bootstrap import RuntimeVersionBootstrap

    svc = RuntimeVersionBootstrap(db_session)
    a = await svc.ensure_baseline(active_agent.tenant_id, active_agent.id)
    b = await svc.ensure_baseline(other_agent.tenant_id, other_agent.id)

    assert a.release_id != b.release_id
    assert a.tenant_id != b.tenant_id

    count = await db_session.scalar(
        select(func.count()).select_from(AgentRuntimeBinding)
    )
    assert count == 2
