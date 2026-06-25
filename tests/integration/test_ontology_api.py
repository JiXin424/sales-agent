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
