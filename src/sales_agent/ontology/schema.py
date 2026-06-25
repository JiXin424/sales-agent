from __future__ import annotations

from sales_agent.ontology.neo4j_client import Neo4jClient


def schema_statements(vector_dimensions: int = 1024) -> list[str]:
    """Cypher statements for constraints and indexes."""
    return [
        """
        CREATE CONSTRAINT entity_canonical_unique IF NOT EXISTS
        FOR (e:Entity)
        REQUIRE (e.tenant_id, e.type, e.canonical_key) IS UNIQUE
        """,
        """
        CREATE INDEX entity_lookup IF NOT EXISTS
        FOR (e:Entity)
        ON (e.tenant_id, e.agent_id, e.type, e.status)
        """,
        """
        CREATE INDEX fact_lookup IF NOT EXISTS
        FOR (f:Fact)
        ON (f.tenant_id, f.agent_id, f.predicate, f.status, f.risk_level)
        """,
        """
        CREATE FULLTEXT INDEX entity_name_fulltext IF NOT EXISTS
        FOR (e:Entity)
        ON EACH [e.name, e.aliases_text]
        """,
        f"""
        CREATE VECTOR INDEX entity_embedding_vector IF NOT EXISTS
        FOR (e:Entity)
        ON (e.embedding)
        OPTIONS {{
          indexConfig: {{
            `vector.dimensions`: {vector_dimensions},
            `vector.similarity_function`: 'cosine'
          }}
        }}
        """,
    ]


async def ensure_ontology_schema(client: Neo4jClient, vector_dimensions: int = 1024) -> None:
    """Create Neo4j ontology constraints and indexes."""
    async with client.session() as session:
        for statement in schema_statements(vector_dimensions):
            await session.run(statement)
