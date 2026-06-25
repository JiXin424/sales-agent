from __future__ import annotations

from typing import Any

from sales_agent.ontology.neo4j_client import Neo4jClient


def upsert_entity_statement() -> str:
    return """
    MERGE (e:Entity {tenant_id: $tenant_id, type: $type, canonical_key: $canonical_key})
    ON CREATE SET
      e.id = $id,
      e.name = $name,
      e.aliases = $aliases,
      e.aliases_text = $aliases_text,
      e.properties = $properties,
      e.embedding = $embedding,
      e.status = $status,
      e.agent_id = $agent_id,
      e.created_at = $now,
      e.updated_at = $now
    ON MATCH SET
      e.name = coalesce(e.name, $name),
      e.aliases = $aliases,
      e.aliases_text = $aliases_text,
      e.properties = $properties,
      e.embedding = coalesce($embedding, e.embedding),
      e.status = coalesce(e.status, $status),
      e.updated_at = $now
    RETURN e.id AS id, e.created_at = $now AS created
    """


def create_fact_statement() -> str:
    return """
    MATCH (s:Entity {tenant_id: $tenant_id, type: $subject_type, canonical_key: $subject_key})
    OPTIONAL MATCH (o:Entity {tenant_id: $tenant_id, type: $object_type, canonical_key: $object_key})
    CREATE (f:Fact {
      id: $fact_id,
      tenant_id: $tenant_id,
      agent_id: $agent_id,
      predicate: $predicate,
      fact_type: $fact_type,
      value: $value,
      properties: $properties,
      confidence: $confidence,
      status: $status,
      risk_level: $risk_level,
      version: $version,
      version_date: $version_date,
      created_at: $now,
      updated_at: $now
    })
    CREATE (s)-[:SUBJECT_OF]->(f)
    FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END |
      CREATE (f)-[:OBJECT_OF]->(o)
    )
    MERGE (d:SourceDocument {id: $source_document_id})
    ON CREATE SET
      d.tenant_id = $tenant_id,
      d.agent_id = $agent_id,
      d.title = $source_title,
      d.source_file_id = $source_file_id,
      d.source_path = $source_path,
      d.content_hash = $content_hash,
      d.status = 'active',
      d.created_at = $now
    CREATE (ev:Evidence {
      tenant_id: $tenant_id,
      id: $evidence_id,
      excerpt: $excerpt,
      locator: $locator,
      confidence: $evidence_confidence,
      extraction_method: $extraction_method,
      created_at: $now
    })
    CREATE (f)-[:SUPPORTED_BY]->(ev)-[:FROM]->(d)
    RETURN f.id AS id
    """


def retrieval_statement() -> str:
    return """
    MATCH (e:Entity)
    WHERE e.tenant_id = $tenant_id
      AND e.status = 'active'
      AND ($agent_id IS NULL OR e.agent_id IS NULL OR e.agent_id = $agent_id)
      AND (toLower(e.name) CONTAINS toLower($query) OR e.aliases_text CONTAINS $query)
    MATCH (e)-[:SUBJECT_OF]->(f:Fact)
    WHERE f.status = 'active'
      AND ($agent_id IS NULL OR f.agent_id IS NULL OR f.agent_id = $agent_id)
    OPTIONAL MATCH (f)-[:OBJECT_OF]->(o:Entity)
    OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(d:SourceDocument)
    RETURN e, f, o, collect(ev) AS evidence, collect(d) AS documents
    LIMIT $limit
    """


def count_entities_by_type_statement() -> str:
    return """
    MATCH (e:Entity)
    WHERE e.tenant_id = $tenant_id
      AND e.status = 'active'
      AND ($agent_id IS NULL OR e.agent_id IS NULL OR e.agent_id = $agent_id)
    RETURN e.type AS type, count(*) AS count
    """


def vector_query_statement() -> str:
    return """
    CALL db.index.vector.queryNodes('entity_embedding_vector', $limit, $embedding)
    YIELD node, score
    WITH node AS e, score
    WHERE e.tenant_id = $tenant_id
      AND e.status = 'active'
      AND ($agent_id IS NULL OR e.agent_id IS NULL OR e.agent_id = $agent_id)
    OPTIONAL MATCH (e)-[:SUBJECT_OF]->(f:Fact)
      WHERE f.status = 'active'
        AND ($agent_id IS NULL OR f.agent_id IS NULL OR f.agent_id = $agent_id)
    OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(d:SourceDocument)
    WITH e, score,
         collect(DISTINCT f) AS facts,
         collect(DISTINCT ev) AS evidence,
         collect(DISTINCT d) AS documents
    RETURN e, score, facts, evidence, documents
    ORDER BY score DESC
    """


class OntologyRepository:
    """Neo4j repository for ontology graph data."""

    def __init__(self, client: Neo4jClient):
        self.client = client

    async def upsert_entity(self, params: dict[str, Any]) -> tuple[str, bool]:
        async with self.client.session() as session:
            result = await session.run(upsert_entity_statement(), params)
            row = await result.single()
            return row["id"], bool(row["created"]) if row else (params["id"], False)

    async def create_fact(self, params: dict[str, Any]) -> str:
        async with self.client.session() as session:
            result = await session.run(create_fact_statement(), params)
            row = await result.single()
            return row["id"] if row else params["fact_id"]

    async def retrieve_by_query(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        async with self.client.session() as session:
            result = await session.run(retrieval_statement(), params)
            return [dict(record) async for record in result]

    async def query_vector(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        async with self.client.session() as session:
            result = await session.run(vector_query_statement(), params)
            return [dict(record) async for record in result]

    async def count_entities_by_type(self, params: dict[str, Any]) -> dict[str, int]:
        """按 Entity.type 分组计数，供探索器头部徽章展示。

        params: {tenant_id, agent_id?}。返回 {type: count, ...}。
        """
        async with self.client.session() as session:
            result = await session.run(count_entities_by_type_statement(), params)
            counts: dict[str, int] = {}
            async for record in result:
                type_name = record["type"] or "unknown"
                counts[type_name] = int(record["count"])
            return counts
