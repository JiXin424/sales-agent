import pytest

from sales_agent.core.config import Neo4jConfig
from sales_agent.ontology.neo4j_client import Neo4jClient


def test_client_disabled_without_uri():
    client = Neo4jClient(Neo4jConfig(uri=""))
    assert client.enabled is False


@pytest.mark.asyncio
async def test_verify_connectivity_disabled_returns_false():
    client = Neo4jClient(Neo4jConfig(uri=""))
    ok, detail = await client.verify_connectivity()
    assert ok is False
    assert detail == "Neo4j URI is not configured"
