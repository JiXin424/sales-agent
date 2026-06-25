import pytest


def test_ontology_router_paths_registered():
    from sales_agent.api.routes.ontology import router

    paths = {route.path for route in router.routes}
    assert "/agents/{agent_id}/ontology/status" in paths
    assert "/agents/{agent_id}/ontology/ingest" in paths
    assert "/agents/{agent_id}/ontology/jobs" in paths


@pytest.mark.asyncio
async def test_ontology_status_not_configured(db_session, sample_tenant):
    from sales_agent.api.routes.ontology import get_ontology_status
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    result = await get_ontology_status(agent.id, db_session)
    assert result["knowledge_engine"] in ("legacy_rag", "ontology_neo4j")
    assert "neo4j_configured" in result


import json
import io
from pathlib import Path
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_ingest_multifile_returns_job_list(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    from sales_agent.main import app
    from sales_agent.api.deps import get_db_session

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/agents/{agent.id}/ontology/ingest",
            files=[("files", ("test1.md", io.BytesIO(b"# test1"), "text/markdown")),
                   ("files", ("test2.md", io.BytesIO(b"# test2"), "text/markdown"))],
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 202
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert "job_id" in data[0]
    assert "filename" in data[0]


@pytest.mark.asyncio
async def test_ingest_reject_non_md(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    from sales_agent.main import app
    from sales_agent.api.deps import get_db_session

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/agents/{agent.id}/ontology/ingest",
            files=[("files", ("bad.exe", io.BytesIO(b"data"), "application/octet-stream"))],
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 400
