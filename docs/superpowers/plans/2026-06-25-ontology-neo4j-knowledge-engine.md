# Neo4j Ontology Knowledge Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing chunk RAG execution path with an internal Neo4j-backed ontology knowledge engine that supports auditable Entity/Fact/Evidence storage, ingestion visualization, conservative entity-vector fallback, and `summary/sections` chat output.

**Architecture:** Keep `sales-agent` as the product shell. Add a focused `sales_agent.ontology` package with Neo4j connection/schema management, LLM extraction, conflict classification, ingestion orchestration, retrieval, and answer generation. `ChatPipeline` continues to decide when retrieval is needed; when it is, it calls `OntologyAnswerService` instead of the legacy chunk `Retriever`.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Neo4j async Python driver, OpenAI-compatible chat/embedding providers already abstracted by `sales-agent`, React + Ant Design + React Query, pytest, Vitest.

---

## Scope Check

This plan implements one cohesive first version: Neo4j Ontology replaces the current RAG runtime path, with ingestion and console visibility. It intentionally does not implement Agentic planner behavior, full `taishan` migration, Neo4j data migration from `Ontology-minimal`, or a custom graph visualization UI.

## File Structure

Backend files to create:

- `src/sales_agent/ontology/__init__.py` - package exports.
- `src/sales_agent/ontology/schemas.py` - dataclasses/Pydantic models for entities, facts, evidence, retrieval, answers, and ingestion stats.
- `src/sales_agent/ontology/neo4j_client.py` - async Neo4j driver wrapper and health check.
- `src/sales_agent/ontology/schema.py` - constraints, full-text index, vector index setup.
- `src/sales_agent/ontology/canonicalizer.py` - canonical key and alias normalization.
- `src/sales_agent/ontology/extractor.py` - LLM prompts and JSON parsing for entity/fact candidates.
- `src/sales_agent/ontology/conflict.py` - deterministic fact conflict classification.
- `src/sales_agent/ontology/repository.py` - Neo4j read/write Cypher for Entity, Fact, Evidence, SourceDocument.
- `src/sales_agent/ontology/ingestion_service.py` - document-to-Neo4j orchestration and job stats.
- `src/sales_agent/ontology/retrieval_service.py` - graph retrieval and conservative vector fallback.
- `src/sales_agent/ontology/answer_service.py` - runtime answer orchestration and `summary/sections` conversion.
- `src/sales_agent/api/routes/ontology.py` - Agent-scoped ontology ingestion/status/admin endpoints.
- `tests/unit/ontology/` - unit tests for new ontology package.
- `tests/integration/test_ontology_chat_pipeline.py` - ChatPipeline integration test with stub ontology answer service.
- `console/src/tests/api/knowledge.test.ts` - frontend API wrapper tests.

Backend files to modify:

- `pyproject.toml` - add `neo4j` dependency.
- `.env.example` - document Neo4j variables if present in repo.
- `config/default.yaml` - add default ontology/neo4j config if this project uses YAML defaults for deploy.
- `src/sales_agent/core/config.py` - add `Neo4jConfig` and `OntologyConfig`.
- `src/sales_agent/api/schemas.py` - add ontology ingestion request/response schemas.
- `src/sales_agent/models/ingestion.py` - add `agent_id`, `engine`, `stage`, ontology stats fields in metadata.
- `src/sales_agent/models/__init__.py` - unchanged unless model additions require exports; update only if adding model classes.
- `src/sales_agent/core/database.py` - no functional change expected; Alembic migration handles new columns.
- `src/sales_agent/api/routes/health.py` - include Neo4j readiness details.
- `src/sales_agent/main.py` - include ontology router.
- `src/sales_agent/services/chat_pipeline.py` - route retrieval path to `OntologyAnswerService`.
- `src/sales_agent/services/response_formatter.py` - no change unless debug/source shape needs helper.
- `src/sales_agent/services/conversation_logger.py` - ensure `graph_evidence` can be stored in model_config metadata.
- `src/sales_agent/migrations/versions/0003_ontology_neo4j_metadata.py` - new Alembic migration.

Frontend files to modify:

- `console/src/api/types.ts` - add ontology job/status/stat interfaces.
- `console/src/api/knowledge.ts` - add Agent-scoped ontology API wrappers.
- `console/src/pages/Agents/AgentKnowledgePage.tsx` - replace document-only page with ingestion dashboard.
- `console/src/tests/api/agents.test.ts` - no change expected.
- `console/src/tests/api/knowledge.test.ts` - new tests for wrappers.

Deployment files to modify:

- `docker-compose.yml` and `docker-compose.generated.yml` only if this implementation chooses to provide a local Neo4j service for development. Keep this separate and optional in Task 11.

---

### Task 1: Configuration And Dependency Foundation

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `config/default.yaml`
- Modify: `src/sales_agent/core/config.py`
- Test: `tests/unit/test_ontology_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/unit/test_ontology_config.py`:

```python
from sales_agent.core.config import Settings


def test_neo4j_config_defaults_disabled():
    settings = Settings()
    assert settings.ontology.knowledge_engine == "legacy_rag"
    assert settings.neo4j.uri == ""
    assert settings.neo4j.database == "neo4j"
    assert settings.ontology.vector_fallback == "conservative"


def test_neo4j_config_from_yaml_fields():
    settings = Settings(
        ontology={"knowledge_engine": "ontology_neo4j", "vector_fallback": "conservative"},
        neo4j={
            "uri": "bolt://neo4j:7687",
            "user": "neo4j",
            "password": "secret",
            "database": "sales",
            "visual_url": "https://neo4j.example/workspace",
        },
    )
    assert settings.ontology.knowledge_engine == "ontology_neo4j"
    assert settings.neo4j.uri == "bolt://neo4j:7687"
    assert settings.neo4j.user == "neo4j"
    assert settings.neo4j.password == "secret"
    assert settings.neo4j.database == "sales"
    assert settings.neo4j.visual_url == "https://neo4j.example/workspace"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_ontology_config.py -v
```

Expected: FAIL because `Settings` has no `ontology` or `neo4j` attributes.

- [ ] **Step 3: Add dependency**

In `pyproject.toml`, add the Neo4j driver to `[project].dependencies`:

```toml
"neo4j>=5.23.0",
```

- [ ] **Step 4: Add config models**

In `src/sales_agent/core/config.py`, add:

```python
class OntologyConfig(BaseModel):
    """Ontology knowledge engine config."""

    knowledge_engine: str = "legacy_rag"  # legacy_rag | ontology_neo4j
    vector_fallback: str = "conservative"


class Neo4jConfig(BaseModel):
    """Neo4j connection and visualization config."""

    uri: str = ""
    user: str = ""
    password: str = ""
    database: str = "neo4j"
    visual_url: str = ""
    connection_timeout_seconds: float = 5.0
```

Add fields to `Settings`:

```python
ontology: OntologyConfig = OntologyConfig()
neo4j: Neo4jConfig = Neo4jConfig()
```

Inside `Settings.from_yaml`, add environment overrides before `instance = cls(**raw)`:

```python
knowledge_engine = os.getenv("KNOWLEDGE_ENGINE")
if knowledge_engine:
    raw.setdefault("ontology", {})["knowledge_engine"] = knowledge_engine

ontology_vector_fallback = os.getenv("ONTOLOGY_VECTOR_FALLBACK")
if ontology_vector_fallback:
    raw.setdefault("ontology", {})["vector_fallback"] = ontology_vector_fallback

neo4j_env = {
    "uri": os.getenv("NEO4J_URI"),
    "user": os.getenv("NEO4J_USER"),
    "password": os.getenv("NEO4J_PASSWORD"),
    "database": os.getenv("NEO4J_DATABASE"),
    "visual_url": os.getenv("NEO4J_VISUAL_URL"),
}
neo4j_overrides = {k: v for k, v in neo4j_env.items() if v}
if neo4j_overrides:
    raw.setdefault("neo4j", {}).update(neo4j_overrides)
```

- [ ] **Step 5: Document env defaults**

In `.env.example`, add:

```dotenv
# Knowledge engine: legacy_rag | ontology_neo4j
KNOWLEDGE_ENGINE=legacy_rag
ONTOLOGY_VECTOR_FALLBACK=conservative

# Neo4j ontology knowledge engine
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
NEO4J_VISUAL_URL=
```

In `config/default.yaml`, add:

```yaml
ontology:
  knowledge_engine: legacy_rag
  vector_fallback: conservative

neo4j:
  uri: ""
  user: "neo4j"
  password: ""
  database: "neo4j"
  visual_url: ""
  connection_timeout_seconds: 5.0
```

- [ ] **Step 6: Run test to verify it passes**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_ontology_config.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example config/default.yaml src/sales_agent/core/config.py tests/unit/test_ontology_config.py
git commit -m "feat: add ontology neo4j configuration"
```

---

### Task 2: Ontology Schemas And Canonicalization

**Files:**
- Create: `src/sales_agent/ontology/__init__.py`
- Create: `src/sales_agent/ontology/schemas.py`
- Create: `src/sales_agent/ontology/canonicalizer.py`
- Test: `tests/unit/ontology/test_canonicalizer.py`
- Test: `tests/unit/ontology/test_schemas.py`

- [ ] **Step 1: Write failing schema and canonicalizer tests**

Create `tests/unit/ontology/test_canonicalizer.py`:

```python
from sales_agent.ontology.canonicalizer import canonical_key, normalize_aliases


def test_canonical_key_lowercases_and_removes_spacing():
    assert canonical_key(" 网票 福多多 ") == "网票福多多"
    assert canonical_key("FDD Product") == "fddproduct"


def test_normalize_aliases_deduplicates_and_keeps_order():
    assert normalize_aliases([" 福多多 ", "福多多", "FDD", ""]) == ["福多多", "FDD"]
```

Create `tests/unit/ontology/test_schemas.py`:

```python
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate, EvidenceItem


def test_entity_candidate_defaults():
    entity = EntityCandidate(type="Product", name="福利卡")
    assert entity.aliases == []
    assert entity.properties == {}
    assert entity.confidence == 0.8


def test_fact_candidate_relation_shape():
    fact = FactCandidate(
        subject_name="福多多",
        predicate="produces",
        object_name="福利卡",
        evidence=[EvidenceItem(excerpt="福多多提供福利卡", locator="doc.md#1")],
    )
    assert fact.fact_type == "relation"
    assert fact.status == "active"
    assert fact.evidence[0].excerpt == "福多多提供福利卡"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_canonicalizer.py tests/unit/ontology/test_schemas.py -v
```

Expected: FAIL because package and classes do not exist.

- [ ] **Step 3: Create schemas**

Create `src/sales_agent/ontology/schemas.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceItem:
    excerpt: str
    locator: str = ""
    confidence: float = 0.8
    extraction_method: str = "llm"
    source_document_id: str | None = None


@dataclass
class EntityCandidate:
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class FactCandidate:
    subject_name: str
    predicate: str
    object_name: str | None = None
    value: str | None = None
    fact_type: str = "relation"
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    status: str = "active"
    risk_level: str = "low"
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class OntologyIngestionStats:
    documents_seen: int = 0
    documents_ingested: int = 0
    entities_created: int = 0
    entities_merged: int = 0
    facts_created: int = 0
    facts_active: int = 0
    facts_pending_review: int = 0
    facts_rejected: int = 0
    conflicts_created: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "documents_seen": self.documents_seen,
            "documents_ingested": self.documents_ingested,
            "entities_created": self.entities_created,
            "entities_merged": self.entities_merged,
            "facts_created": self.facts_created,
            "facts_active": self.facts_active,
            "facts_pending_review": self.facts_pending_review,
            "facts_rejected": self.facts_rejected,
            "conflicts_created": self.conflicts_created,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class GraphEvidence:
    ontology_intent: str
    center_entities: list[dict[str, Any]] = field(default_factory=list)
    matched_entities: list[dict[str, Any]] = field(default_factory=list)
    facts_used: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_documents: list[dict[str, Any]] = field(default_factory=list)
    retrieval_strategy: str = "graph"
    vector_fallback_used: bool = False
    confidence: float = 0.0
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology_intent": self.ontology_intent,
            "center_entities": self.center_entities,
            "matched_entities": self.matched_entities,
            "facts_used": self.facts_used,
            "evidence": self.evidence,
            "source_documents": self.source_documents,
            "retrieval_strategy": self.retrieval_strategy,
            "vector_fallback_used": self.vector_fallback_used,
            "confidence": self.confidence,
            "timings_ms": self.timings_ms,
        }


@dataclass
class OntologyAnswer:
    answer: dict[str, Any]
    sources: list[dict[str, Any]]
    graph_evidence: GraphEvidence
```

- [ ] **Step 4: Create canonicalizer**

Create `src/sales_agent/ontology/canonicalizer.py`:

```python
from __future__ import annotations

import re


_SPACE_RE = re.compile(r"\s+")


def canonical_key(name: str) -> str:
    """Build a stable key for entity de-duplication."""
    return _SPACE_RE.sub("", (name or "").strip().lower())


def normalize_aliases(aliases: list[str] | None) -> list[str]:
    """Trim aliases, remove blanks, de-duplicate while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases or []:
        clean = alias.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out
```

Create `src/sales_agent/ontology/__init__.py`:

```python
"""Neo4j-backed ontology knowledge engine."""
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_canonicalizer.py tests/unit/ontology/test_schemas.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/ontology tests/unit/ontology
git commit -m "feat: add ontology schema primitives"
```

---

### Task 3: Neo4j Client And Schema Setup

**Files:**
- Create: `src/sales_agent/ontology/neo4j_client.py`
- Create: `src/sales_agent/ontology/schema.py`
- Test: `tests/unit/ontology/test_neo4j_client.py`
- Test: `tests/unit/ontology/test_neo4j_schema.py`

- [ ] **Step 1: Write failing tests using fake sessions**

Create `tests/unit/ontology/test_neo4j_client.py`:

```python
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
```

Create `tests/unit/ontology/test_neo4j_schema.py`:

```python
import pytest

from sales_agent.ontology.schema import schema_statements


def test_schema_statements_include_fact_and_vector_indexes():
    statements = schema_statements(vector_dimensions=1024)
    joined = "\n".join(statements)
    assert "entity_canonical_unique" in joined
    assert "entity_name_fulltext" in joined
    assert "entity_embedding_vector" in joined
    assert "fact_lookup" in joined
    assert "VECTOR" in joined
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_neo4j_client.py tests/unit/ontology/test_neo4j_schema.py -v
```

Expected: FAIL because files do not exist.

- [ ] **Step 3: Implement Neo4j client**

Create `src/sales_agent/ontology/neo4j_client.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neo4j import AsyncDriver, AsyncGraphDatabase

from sales_agent.core.config import Neo4jConfig


class Neo4jClient:
    """Small async Neo4j driver wrapper."""

    def __init__(self, config: Neo4jConfig):
        self.config = config
        self._driver: AsyncDriver | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.uri)

    def driver(self) -> AsyncDriver:
        if not self.enabled:
            raise RuntimeError("Neo4j URI is not configured")
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                connection_timeout=self.config.connection_timeout_seconds,
            )
        return self._driver

    @asynccontextmanager
    async def session(self) -> AsyncIterator:
        async with self.driver().session(database=self.config.database) as session:
            yield session

    async def verify_connectivity(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Neo4j URI is not configured"
        try:
            await self.driver().verify_connectivity()
            return True, "ok"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
```

- [ ] **Step 4: Implement schema statements**

Create `src/sales_agent/ontology/schema.py`:

```python
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
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_neo4j_client.py tests/unit/ontology/test_neo4j_schema.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/ontology/neo4j_client.py src/sales_agent/ontology/schema.py tests/unit/ontology/test_neo4j_client.py tests/unit/ontology/test_neo4j_schema.py
git commit -m "feat: add neo4j ontology client"
```

---

### Task 4: Neo4j Repository For Entity/Fact/Evidence

**Files:**
- Create: `src/sales_agent/ontology/repository.py`
- Test: `tests/unit/ontology/test_repository_cypher.py`

- [ ] **Step 1: Write failing repository Cypher tests**

Create `tests/unit/ontology/test_repository_cypher.py`:

```python
from sales_agent.ontology.repository import (
    upsert_entity_statement,
    create_fact_statement,
    retrieval_statement,
    vector_query_statement,
)


def test_upsert_entity_statement_uses_canonical_unique_key():
    stmt = upsert_entity_statement()
    assert "MERGE (e:Entity" in stmt
    assert "tenant_id: $tenant_id" in stmt
    assert "canonical_key: $canonical_key" in stmt
    assert "type: $type" in stmt


def test_create_fact_statement_uses_fact_node_model():
    stmt = create_fact_statement()
    assert "CREATE (f:Fact" in stmt
    assert "SUBJECT_OF" in stmt
    assert "OBJECT_OF" in stmt
    assert "SUPPORTED_BY" in stmt
    assert "SourceDocument" in stmt


def test_retrieval_statement_filters_active_facts():
    stmt = retrieval_statement()
    assert "f.status = 'active'" in stmt
    assert "e.tenant_id = $tenant_id" in stmt


def test_vector_query_statement_uses_vector_index():
    stmt = vector_query_statement()
    assert "db.index.vector.queryNodes" in stmt
    assert "entity_embedding_vector" in stmt
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_repository_cypher.py -v
```

Expected: FAIL because repository does not exist.

- [ ] **Step 3: Implement repository statements and methods**

Create `src/sales_agent/ontology/repository.py` with these statement builders first:

```python
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
    MATCH (e:Entity {tenant_id: $tenant_id})
    WHERE e.status = 'active'
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


def vector_query_statement() -> str:
    return """
    CALL db.index.vector.queryNodes('entity_embedding_vector', $limit, $embedding)
    YIELD node, score
    WHERE node.tenant_id = $tenant_id
      AND node.status = 'active'
      AND ($agent_id IS NULL OR node.agent_id IS NULL OR node.agent_id = $agent_id)
    RETURN node AS e, score
    ORDER BY score DESC
    """
```

Then add the repository shell:

```python
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
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_repository_cypher.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/repository.py tests/unit/ontology/test_repository_cypher.py
git commit -m "feat: add ontology neo4j repository"
```

---

### Task 5: LLM Extraction And Answer Formatting

**Files:**
- Create: `src/sales_agent/ontology/extractor.py`
- Create: `src/sales_agent/ontology/answer_service.py` initial formatting helpers only
- Test: `tests/unit/ontology/test_extractor.py`
- Test: `tests/unit/ontology/test_answer_format.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/ontology/test_extractor.py`:

```python
from sales_agent.ontology.extractor import parse_entities_json, parse_facts_json


def test_parse_entities_json_handles_code_block():
    raw = '```json\n{"entities":[{"type":"Product","name":"福利卡","aliases":["卡"],"properties":{"price":"100"}}]}\n```'
    entities = parse_entities_json(raw)
    assert entities[0].type == "Product"
    assert entities[0].name == "福利卡"
    assert entities[0].aliases == ["卡"]


def test_parse_facts_json_handles_attribute_fact():
    raw = '{"facts":[{"subject_name":"福利卡","predicate":"price","value":"100元","fact_type":"attribute"}]}'
    facts = parse_facts_json(raw)
    assert facts[0].subject_name == "福利卡"
    assert facts[0].predicate == "price"
    assert facts[0].value == "100元"
```

Create `tests/unit/ontology/test_answer_format.py`:

```python
from sales_agent.ontology.answer_service import ontology_answer_to_sections


def test_ontology_answer_to_sections_keeps_summary():
    result = ontology_answer_to_sections(
        {"answer": "福多多的核心优势是供应稳定。", "evidence": ["证据A"], "confidence": 0.9}
    )
    assert result["summary"] == "福多多的核心优势是供应稳定。"
    assert result["sections"][0]["title"] == "依据摘要"
    assert "证据A" in result["sections"][0]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_extractor.py tests/unit/ontology/test_answer_format.py -v
```

Expected: FAIL because modules/functions do not exist.

- [ ] **Step 3: Implement extractor parsing**

Create `src/sales_agent/ontology/extractor.py`:

```python
from __future__ import annotations

import json
import re
from typing import Any

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import EntityCandidate, EvidenceItem, FactCandidate


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _evidence_items(items: list[dict[str, Any]] | None) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            excerpt=str(item.get("excerpt", "")),
            locator=str(item.get("locator", "")),
            confidence=float(item.get("confidence", 0.8)),
            extraction_method=str(item.get("extraction_method", "llm")),
        )
        for item in (items or [])
        if item.get("excerpt")
    ]


def parse_entities_json(raw: str) -> list[EntityCandidate]:
    data = _extract_json(raw)
    return [
        EntityCandidate(
            type=str(item.get("type", "Concept")),
            name=str(item.get("name", "")).strip(),
            aliases=[str(a) for a in item.get("aliases", []) if a],
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            confidence=float(item.get("confidence", 0.8)),
            evidence=_evidence_items(item.get("evidence")),
        )
        for item in data.get("entities", data.get("objects", []))
        if str(item.get("name", "")).strip()
    ]


def parse_facts_json(raw: str) -> list[FactCandidate]:
    data = _extract_json(raw)
    return [
        FactCandidate(
            subject_name=str(item.get("subject_name", item.get("subject", ""))).strip(),
            predicate=str(item.get("predicate", item.get("type", "related_to"))),
            object_name=(str(item.get("object_name", item.get("object", ""))).strip() or None),
            value=(str(item.get("value", "")).strip() or None),
            fact_type=str(item.get("fact_type", "relation")),
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            confidence=float(item.get("confidence", 0.8)),
            status=str(item.get("status", "active")),
            risk_level=str(item.get("risk_level", "low")),
            evidence=_evidence_items(item.get("evidence")),
        )
        for item in data.get("facts", data.get("relations", []))
        if str(item.get("subject_name", item.get("subject", ""))).strip()
    ]
```

Add prompt-backed functions below the parsing functions:

```python
ENTITY_EXTRACTION_PROMPT = """你是销售知识本体抽取专家。请从文档中抽取实体。
输出 JSON：{"entities":[{"type":"Product","name":"实体名","aliases":[],"properties":{},"confidence":0.8,"evidence":[{"excerpt":"原文片段","locator":"位置"}]}]}
只抽取文档明确提到的信息。

文档：
{content}
"""

FACT_EXTRACTION_PROMPT = """你是销售知识本体抽取专家。请从文档和实体列表中抽取可审计事实。
输出 JSON：{"facts":[{"subject_name":"主体","predicate":"关系或属性","object_name":"客体","value":null,"fact_type":"relation","confidence":0.8,"risk_level":"low","evidence":[{"excerpt":"原文片段","locator":"位置"}]}]}
高风险事实包括价格承诺、交付承诺、资质认证、政策条款、合规边界和关键技术指标。

实体：
{entities_json}

文档：
{content}
"""


async def extract_entities(chat_model: ChatModel, content: str) -> list[EntityCandidate]:
    raw = await chat_model.generate(
        messages=[{"role": "user", "content": ENTITY_EXTRACTION_PROMPT.format(content=content[:6000])}],
        temperature=0.1,
        max_tokens=3000,
    )
    return parse_entities_json(raw)


async def extract_facts(
    chat_model: ChatModel,
    content: str,
    entities: list[EntityCandidate],
) -> list[FactCandidate]:
    entities_json = json.dumps(
        [{"type": e.type, "name": e.name, "aliases": e.aliases, "properties": e.properties} for e in entities],
        ensure_ascii=False,
    )
    raw = await chat_model.generate(
        messages=[{"role": "user", "content": FACT_EXTRACTION_PROMPT.format(content=content[:6000], entities_json=entities_json)}],
        temperature=0.1,
        max_tokens=4000,
    )
    return parse_facts_json(raw)
```

- [ ] **Step 4: Implement answer formatting helper**

Create initial `src/sales_agent/ontology/answer_service.py`:

```python
from __future__ import annotations

from typing import Any


def ontology_answer_to_sections(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert ontology answer JSON to sales-agent summary/sections."""
    answer = str(raw.get("answer", "")).strip()
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
    sections: list[dict[str, str]] = []
    if evidence:
        sections.append({
            "title": "依据摘要",
            "content": "\n".join(f"- {item}" for item in evidence if item),
        })
    confidence = raw.get("confidence")
    if confidence is not None:
        sections.append({"title": "可信度", "content": str(confidence)})
    return {"summary": answer, "sections": sections}
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_extractor.py tests/unit/ontology/test_answer_format.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/ontology/extractor.py src/sales_agent/ontology/answer_service.py tests/unit/ontology/test_extractor.py tests/unit/ontology/test_answer_format.py
git commit -m "feat: add ontology extraction parsing"
```

---

### Task 6: Conflict Classification

**Files:**
- Create: `src/sales_agent/ontology/conflict.py`
- Test: `tests/unit/ontology/test_conflict.py`

- [ ] **Step 1: Write failing conflict tests**

Create `tests/unit/ontology/test_conflict.py`:

```python
from sales_agent.ontology.conflict import classify_fact_risk, merge_status_for_risk
from sales_agent.ontology.schemas import FactCandidate


def test_high_risk_for_certification_and_price_commitment():
    cert = FactCandidate(subject_name="产品A", predicate="certified_for", value="三类证")
    price = FactCandidate(subject_name="产品A", predicate="price_commitment", value="保证最低价")
    assert classify_fact_risk(cert) == "high"
    assert classify_fact_risk(price) == "high"


def test_medium_risk_for_price_range():
    fact = FactCandidate(subject_name="产品A", predicate="price", value="10-20万")
    assert classify_fact_risk(fact) == "medium"


def test_low_risk_for_alias_or_description():
    fact = FactCandidate(subject_name="产品A", predicate="alias", value="A产品")
    assert classify_fact_risk(fact) == "low"


def test_merge_status_for_risk():
    assert merge_status_for_risk("low") == "active"
    assert merge_status_for_risk("medium") == "pending_review"
    assert merge_status_for_risk("high") == "pending_review"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_conflict.py -v
```

Expected: FAIL because `conflict.py` does not exist.

- [ ] **Step 3: Implement deterministic risk classification**

Create `src/sales_agent/ontology/conflict.py`:

```python
from __future__ import annotations

from sales_agent.ontology.schemas import FactCandidate


HIGH_RISK_PREDICATES = {
    "certified_for",
    "certification",
    "policy_clause",
    "delivery_commitment",
    "price_commitment",
    "compliance_boundary",
    "technical_metric",
}

MEDIUM_RISK_PREDICATES = {
    "price",
    "pricing",
    "case_metric",
    "competitor_claim",
    "performance_metric",
}


def classify_fact_risk(fact: FactCandidate) -> str:
    predicate = fact.predicate.lower()
    text = f"{fact.value or ''} {fact.object_name or ''}".lower()
    if predicate in HIGH_RISK_PREDICATES:
        return "high"
    if any(word in text for word in ("保证", "最低价", "一周上线", "三类证", "政策规定")):
        return "high"
    if predicate in MEDIUM_RISK_PREDICATES:
        return "medium"
    return "low"


def merge_status_for_risk(risk_level: str) -> str:
    return "active" if risk_level == "low" else "pending_review"
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_conflict.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/conflict.py tests/unit/ontology/test_conflict.py
git commit -m "feat: classify ontology fact conflicts"
```

---

### Task 7: Ingestion Metadata And API Schemas

**Files:**
- Modify: `src/sales_agent/models/ingestion.py`
- Modify: `src/sales_agent/api/schemas.py`
- Create: `src/sales_agent/migrations/versions/0003_ontology_neo4j_metadata.py`
- Test: `tests/unit/test_ingestion_job.py`

- [ ] **Step 1: Extend existing ingestion tests**

Add to `tests/unit/test_ingestion_job.py`:

```python
def test_ingestion_job_accepts_ontology_metadata():
    from sales_agent.models.ingestion import IngestionJob

    job = IngestionJob(
        tenant_id="t1",
        agent_id="a1",
        engine="ontology_neo4j",
        stage="extracting_entities",
        entities_created=2,
        facts_created=3,
        conflicts_created=1,
    )
    assert job.agent_id == "a1"
    assert job.engine == "ontology_neo4j"
    assert job.stage == "extracting_entities"
    assert job.entities_created == 2
    assert job.facts_created == 3
    assert job.conflicts_created == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_ingestion_job.py::test_ingestion_job_accepts_ontology_metadata -v
```

Expected: FAIL because fields do not exist.

- [ ] **Step 3: Add model fields**

In `src/sales_agent/models/ingestion.py`, add fields to `IngestionJob`:

```python
agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
engine: Mapped[str] = mapped_column(Text, nullable=False, default="legacy_rag")
stage: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
entities_created: Mapped[int] = mapped_column(nullable=False, default=0)
entities_merged: Mapped[int] = mapped_column(nullable=False, default=0)
facts_created: Mapped[int] = mapped_column(nullable=False, default=0)
facts_active: Mapped[int] = mapped_column(nullable=False, default=0)
facts_pending_review: Mapped[int] = mapped_column(nullable=False, default=0)
facts_rejected: Mapped[int] = mapped_column(nullable=False, default=0)
conflicts_created: Mapped[int] = mapped_column(nullable=False, default=0)
```

- [ ] **Step 4: Add Alembic migration**

Create `src/sales_agent/migrations/versions/0003_ontology_neo4j_metadata.py`:

```python
"""add ontology metadata to ingestion_jobs

Revision ID: 0003_ontology_neo4j_metadata
Revises: 0002_prompt_category
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_ontology_neo4j_metadata"
down_revision: Union[str, None] = "0002_prompt_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("agent_id", sa.Text(), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("engine", sa.Text(), nullable=False, server_default="legacy_rag"))
    op.add_column("ingestion_jobs", sa.Column("stage", sa.Text(), nullable=False, server_default="queued"))
    op.add_column("ingestion_jobs", sa.Column("entities_created", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("entities_merged", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_created", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_active", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_pending_review", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("facts_rejected", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("conflicts_created", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_ingestion_jobs_agent_id", "ingestion_jobs", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_agent_id", table_name="ingestion_jobs")
    for column in (
        "conflicts_created",
        "facts_rejected",
        "facts_pending_review",
        "facts_active",
        "facts_created",
        "entities_merged",
        "entities_created",
        "stage",
        "engine",
        "agent_id",
    ):
        op.drop_column("ingestion_jobs", column)
```

- [ ] **Step 5: Add API schemas**

In `src/sales_agent/api/schemas.py`, add:

```python
class OntologyIngestRequest(BaseModel):
    path: str
    rebuild: bool = False


class OntologyJobResponse(BaseModel):
    id: str
    tenant_id: str
    agent_id: str | None = None
    engine: str = "ontology_neo4j"
    status: str
    stage: str
    documents_seen: int = 0
    documents_ingested: int = 0
    entities_created: int = 0
    entities_merged: int = 0
    facts_created: int = 0
    facts_active: int = 0
    facts_pending_review: int = 0
    facts_rejected: int = 0
    conflicts_created: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class OntologyStatusResponse(BaseModel):
    knowledge_engine: str
    ontology_status: str
    neo4j_configured: bool
    neo4j_ready: bool
    visual_url: str = ""
```

- [ ] **Step 6: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_ingestion_job.py::test_ingestion_job_accepts_ontology_metadata -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/models/ingestion.py src/sales_agent/api/schemas.py src/sales_agent/migrations/versions/0003_ontology_neo4j_metadata.py tests/unit/test_ingestion_job.py
git commit -m "feat: add ontology ingestion metadata"
```

---

### Task 8: Ontology Ingestion Service

**Files:**
- Create: `src/sales_agent/ontology/ingestion_service.py`
- Test: `tests/unit/ontology/test_ingestion_service.py`

- [ ] **Step 1: Write failing ingestion orchestration test**

Create `tests/unit/ontology/test_ingestion_service.py`:

```python
import json
from pathlib import Path

import pytest

from sales_agent.ontology.ingestion_service import OntologyIngestionService
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate


class FakeExtractor:
    async def extract_entities(self, content):
        return [EntityCandidate(type="Product", name="福利卡")]

    async def extract_facts(self, content, entities):
        return [FactCandidate(subject_name="福利卡", predicate="description", value="员工福利产品", fact_type="attribute")]


class FakeEmbedding:
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class FakeRepository:
    def __init__(self):
        self.entities = []
        self.facts = []

    async def upsert_entity(self, params):
        self.entities.append(params)
        return params["id"], True

    async def create_fact(self, params):
        self.facts.append(params)
        return params["fact_id"]


@pytest.mark.asyncio
async def test_ingest_markdown_file_writes_entity_and_fact(tmp_path, db_session, sample_tenant):
    path = tmp_path / "sample.md"
    path.write_text("# 福利卡\n福多多提供员工福利产品。", encoding="utf-8")
    repo = FakeRepository()
    service = OntologyIngestionService(
        db=db_session,
        repository=repo,
        embedding_model=FakeEmbedding(),
        extractor=FakeExtractor(),
    )

    job, stats = await service.ingest_paths(
        tenant_id=sample_tenant,
        agent_id="agent1",
        paths=[path],
    )

    assert job.engine == "ontology_neo4j"
    assert job.status == "completed"
    assert stats.entities_created == 1
    assert stats.facts_created == 1
    assert repo.entities[0]["tenant_id"] == sample_tenant
    assert repo.facts[0]["predicate"] == "description"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_ingestion_service.py -v
```

Expected: FAIL because ingestion service does not exist.

- [ ] **Step 3: Implement ingestion service**

Create `src/sales_agent/ontology/ingestion_service.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.base import EmbeddingModel
from sales_agent.models.base import generate_id, utcnow
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.canonicalizer import canonical_key, normalize_aliases
from sales_agent.ontology.conflict import classify_fact_risk, merge_status_for_risk
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate, OntologyIngestionStats


class ExtractorProtocol(Protocol):
    async def extract_entities(self, content: str) -> list[EntityCandidate]: ...
    async def extract_facts(self, content: str, entities: list[EntityCandidate]) -> list[FactCandidate]: ...


class RepositoryProtocol(Protocol):
    async def upsert_entity(self, params: dict) -> tuple[str, bool]: ...
    async def create_fact(self, params: dict) -> str: ...


class OntologyIngestionService:
    def __init__(
        self,
        db: AsyncSession,
        repository: RepositoryProtocol,
        embedding_model: EmbeddingModel,
        extractor: ExtractorProtocol,
    ) -> None:
        self.db = db
        self.repository = repository
        self.embedding_model = embedding_model
        self.extractor = extractor

    async def ingest_paths(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        paths: list[Path],
    ) -> tuple[IngestionJob, OntologyIngestionStats]:
        job = IngestionJob(
            tenant_id=tenant_id,
            agent_id=agent_id,
            engine="ontology_neo4j",
            status="running",
            stage="uploaded",
            documents_seen=len(paths),
        )
        self.db.add(job)
        await self.db.flush()

        stats = OntologyIngestionStats(documents_seen=len(paths))
        for path in paths:
            try:
                await self._ingest_one(job, stats, tenant_id, agent_id, path)
                stats.documents_ingested += 1
            except Exception as exc:  # noqa: BLE001
                stats.errors.append({"file": str(path), "error": str(exc)})

        job.documents_ingested = stats.documents_ingested
        job.entities_created = stats.entities_created
        job.entities_merged = stats.entities_merged
        job.facts_created = stats.facts_created
        job.facts_active = stats.facts_active
        job.facts_pending_review = stats.facts_pending_review
        job.facts_rejected = stats.facts_rejected
        job.conflicts_created = stats.conflicts_created
        job.warnings_json = json.dumps(stats.warnings, ensure_ascii=False)
        job.errors_json = json.dumps(stats.errors, ensure_ascii=False)
        job.metadata_json = json.dumps(stats.to_metadata(), ensure_ascii=False)
        job.status = "completed" if not stats.errors else "completed_with_errors"
        job.stage = "completed" if not stats.errors else "completed_with_warnings"
        await self.db.flush()
        return job, stats

    async def _ingest_one(
        self,
        job: IngestionJob,
        stats: OntologyIngestionStats,
        tenant_id: str,
        agent_id: str | None,
        path: Path,
    ) -> None:
        job.stage = "parsed"
        content = path.read_text(encoding="utf-8")
        source_document_id = generate_id()
        now = utcnow()

        job.stage = "extracting_entities"
        entities = await self.extractor.extract_entities(content)
        embeddings = await self.embedding_model.embed([
            f"{e.name} {json.dumps(e.properties, ensure_ascii=False)}" for e in entities
        ]) if entities else []

        name_to_entity: dict[str, EntityCandidate] = {}
        for entity, embedding in zip(entities, embeddings):
            aliases = normalize_aliases(entity.aliases)
            entity_key = canonical_key(entity.name)
            entity_id, created = await self.repository.upsert_entity({
                "id": generate_id(),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "type": entity.type,
                "name": entity.name,
                "canonical_key": entity_key,
                "aliases": aliases,
                "aliases_text": " ".join(aliases),
                "properties": json.dumps(entity.properties, ensure_ascii=False),
                "embedding": embedding,
                "status": "active",
                "now": now,
            })
            name_to_entity[entity.name] = entity
            if created:
                stats.entities_created += 1
            else:
                stats.entities_merged += 1

        job.stage = "extracting_facts"
        facts = await self.extractor.extract_facts(content, entities)
        job.stage = "writing_neo4j"
        for fact in facts:
            subject = name_to_entity.get(fact.subject_name)
            if subject is None:
                stats.warnings.append(f"Fact skipped; subject not found: {fact.subject_name}")
                continue
            object_entity = name_to_entity.get(fact.object_name or "")
            risk = classify_fact_risk(fact)
            status = merge_status_for_risk(risk)
            if status == "active":
                stats.facts_active += 1
            else:
                stats.facts_pending_review += 1
                if risk == "high":
                    stats.conflicts_created += 1
            evidence = fact.evidence[0] if fact.evidence else None
            await self.repository.create_fact({
                "fact_id": generate_id(),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "subject_type": subject.type,
                "subject_key": canonical_key(subject.name),
                "object_type": object_entity.type if object_entity else "",
                "object_key": canonical_key(object_entity.name) if object_entity else "",
                "predicate": fact.predicate,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "properties": json.dumps(fact.properties, ensure_ascii=False),
                "confidence": fact.confidence,
                "status": status,
                "risk_level": risk,
                "version": 1,
                "version_date": None,
                "source_document_id": source_document_id,
                "source_title": path.name,
                "source_file_id": None,
                "source_path": str(path),
                "content_hash": "",
                "evidence_id": generate_id(),
                "excerpt": evidence.excerpt if evidence else content[:300],
                "locator": evidence.locator if evidence else path.name,
                "evidence_confidence": evidence.confidence if evidence else fact.confidence,
                "extraction_method": evidence.extraction_method if evidence else "llm",
                "now": now,
            })
            stats.facts_created += 1
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_ingestion_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/ingestion_service.py tests/unit/ontology/test_ingestion_service.py
git commit -m "feat: add ontology ingestion service"
```

---

### Task 9: Retrieval And Answer Service

**Files:**
- Modify: `src/sales_agent/ontology/answer_service.py`
- Create: `src/sales_agent/ontology/retrieval_service.py`
- Test: `tests/unit/ontology/test_retrieval_service.py`
- Test: `tests/unit/ontology/test_answer_service.py`

- [ ] **Step 1: Write failing retrieval tests**

Create `tests/unit/ontology/test_retrieval_service.py`:

```python
import pytest

from sales_agent.ontology.retrieval_service import OntologyRetrievalService


class FakeRepository:
    def __init__(self, rows):
        self.rows = rows
        self.vector_called = False

    async def retrieve_by_query(self, params):
        return self.rows

    async def query_vector(self, params):
        self.vector_called = True
        return [{"e": {"id": "vec1", "name": "向量实体", "type": "Product"}, "score": 0.91}]


class FakeEmbedding:
    async def embed(self, texts):
        return [[0.1] * 1024]


@pytest.mark.asyncio
async def test_vector_fallback_not_used_when_graph_has_rows():
    repo = FakeRepository(rows=[{"e": {"id": "e1"}, "f": {"id": "f1"}, "o": None, "evidence": [], "documents": []}])
    service = OntologyRetrievalService(repo, FakeEmbedding())
    evidence = await service.retrieve(tenant_id="t1", agent_id="a1", question="福利卡")
    assert evidence.vector_fallback_used is False
    assert repo.vector_called is False


@pytest.mark.asyncio
async def test_vector_fallback_used_when_graph_empty():
    repo = FakeRepository(rows=[])
    service = OntologyRetrievalService(repo, FakeEmbedding())
    evidence = await service.retrieve(tenant_id="t1", agent_id="a1", question="福利卡")
    assert evidence.vector_fallback_used is True
    assert repo.vector_called is True
```

Create `tests/unit/ontology/test_answer_service.py`:

```python
import pytest

from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.schemas import GraphEvidence


class FakeRetrieval:
    async def retrieve(self, tenant_id, agent_id, question):
        return GraphEvidence(
            ontology_intent="entity_info",
            center_entities=[{"name": "福利卡"}],
            facts_used=[{"predicate": "description", "value": "员工福利产品"}],
            confidence=0.9,
        )


class FakeChat:
    async def generate(self, messages, temperature=0.3, max_tokens=2000):
        return '{"answer":"福利卡是员工福利产品。","evidence":["description: 员工福利产品"],"confidence":0.9}'


@pytest.mark.asyncio
async def test_answer_service_returns_summary_sections():
    service = OntologyAnswerService(retrieval=FakeRetrieval(), chat_model=FakeChat())
    result = await service.answer_for_task(
        tenant_id="t1",
        agent_id="a1",
        task_type="knowledge_qa",
        message="福利卡是什么",
    )
    assert result.answer["summary"] == "福利卡是员工福利产品。"
    assert result.graph_evidence.confidence == 0.9
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_retrieval_service.py tests/unit/ontology/test_answer_service.py -v
```

Expected: FAIL because retrieval service and class methods do not exist.

- [ ] **Step 3: Implement retrieval service**

Create `src/sales_agent/ontology/retrieval_service.py`:

```python
from __future__ import annotations

import time
from typing import Protocol

from sales_agent.llm.base import EmbeddingModel
from sales_agent.ontology.schemas import GraphEvidence


class RepositoryProtocol(Protocol):
    async def retrieve_by_query(self, params: dict) -> list[dict]: ...
    async def query_vector(self, params: dict) -> list[dict]: ...


class OntologyRetrievalService:
    def __init__(self, repository: RepositoryProtocol, embedding_model: EmbeddingModel, limit: int = 30):
        self.repository = repository
        self.embedding_model = embedding_model
        self.limit = limit

    async def retrieve(self, *, tenant_id: str, agent_id: str | None, question: str) -> GraphEvidence:
        started = time.monotonic()
        rows = await self.repository.retrieve_by_query({
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "query": question,
            "limit": self.limit,
        })
        vector_used = False
        matched_entities = [self._node(row.get("e")) for row in rows if row.get("e")]
        facts = [self._node(row.get("f")) for row in rows if row.get("f")]
        documents = []
        evidence = []
        for row in rows:
            documents.extend([self._node(d) for d in row.get("documents", []) if d])
            evidence.extend([self._node(ev) for ev in row.get("evidence", []) if ev])

        if not matched_entities:
            embedding = (await self.embedding_model.embed([question]))[0]
            vector_rows = await self.repository.query_vector({
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "embedding": embedding,
                "limit": 5,
            })
            vector_used = True
            matched_entities.extend([self._node(row.get("e")) for row in vector_rows if row.get("e")])

        return GraphEvidence(
            ontology_intent="entity_info",
            center_entities=matched_entities[:5],
            matched_entities=matched_entities,
            facts_used=facts,
            evidence=evidence,
            source_documents=documents,
            retrieval_strategy="graph_vector_fallback" if vector_used else "graph",
            vector_fallback_used=vector_used,
            confidence=0.8 if matched_entities else 0.0,
            timings_ms={"ontology_retrieval": int((time.monotonic() - started) * 1000)},
        )

    def _node(self, value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return dict(value)
        except Exception:  # noqa: BLE001
            return {"value": str(value)}
```

- [ ] **Step 4: Complete answer service**

Append to `src/sales_agent/ontology/answer_service.py`:

```python
import json
from typing import Protocol

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer


class RetrievalProtocol(Protocol):
    async def retrieve(self, *, tenant_id: str, agent_id: str | None, question: str) -> GraphEvidence: ...


ONTOLOGY_RESPONSE_PROMPT = """你是销售知识图谱回答器。基于图谱事实回答用户问题，不要编造。

图谱证据：
{graph_json}

用户问题：{question}
任务类型：{task_type}

输出 JSON：
{{"answer":"自然语言回答","evidence":["使用的事实或来源"],"confidence":0.8}}
"""


class OntologyAnswerService:
    def __init__(self, retrieval: RetrievalProtocol, chat_model: ChatModel):
        self.retrieval = retrieval
        self.chat_model = chat_model

    async def answer_for_task(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        task_type: str,
        message: str,
    ) -> OntologyAnswer:
        graph_evidence = await self.retrieval.retrieve(
            tenant_id=tenant_id,
            agent_id=agent_id,
            question=message,
        )
        raw = await self.chat_model.generate(
            messages=[{
                "role": "user",
                "content": ONTOLOGY_RESPONSE_PROMPT.format(
                    graph_json=json.dumps(graph_evidence.to_dict(), ensure_ascii=False),
                    question=message,
                    task_type=task_type,
                ),
            }],
            temperature=0.2,
            max_tokens=1600,
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"answer": raw, "evidence": [], "confidence": graph_evidence.confidence}
        answer = ontology_answer_to_sections(parsed)
        sources = [
            {
                "document_id": doc.get("id", ""),
                "title": doc.get("title", "图谱来源"),
                "display_title": doc.get("title", "图谱来源"),
                "score": graph_evidence.confidence,
                "source_type": "ontology",
            }
            for doc in graph_evidence.source_documents[:3]
        ]
        return OntologyAnswer(answer=answer, sources=sources, graph_evidence=graph_evidence)
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology/test_retrieval_service.py tests/unit/ontology/test_answer_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/ontology/retrieval_service.py src/sales_agent/ontology/answer_service.py tests/unit/ontology/test_retrieval_service.py tests/unit/ontology/test_answer_service.py
git commit -m "feat: add ontology retrieval answer service"
```

---

### Task 10: ChatPipeline Integration

**Files:**
- Modify: `src/sales_agent/services/chat_pipeline.py`
- Test: `tests/integration/test_ontology_chat_pipeline.py`

- [ ] **Step 1: Write failing ChatPipeline test with monkeypatch**

Create `tests/integration/test_ontology_chat_pipeline.py`:

```python
import pytest

from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer


@pytest.mark.asyncio
async def test_chat_pipeline_uses_ontology_when_configured(monkeypatch, db_session, sample_tenant):
    from sales_agent.core.config import Settings
    from sales_agent.models.tenant import Tenant
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    from sales_agent.services.chat_pipeline import ChatPipeline

    await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")

    class FakeTenantResolver:
        def __init__(self, db):
            self.db = db

        async def resolve(self, tenant_id):
            return {"tenant_id": tenant_id, "config": {}}

        def get_model_provider(self, tenant_info):
            class Provider:
                class Chat:
                    async def generate(self, *args, **kwargs):
                        return '{"summary":"unused","sections":[]}'
                class Embedding:
                    async def embed(self, texts):
                        return [[0.1] * 1024 for _ in texts]
                chat = Chat()
                embedding = Embedding()
            return Provider()

    class FakeOntologyAnswerService:
        def __init__(self, *args, **kwargs):
            pass

        async def answer_for_task(self, tenant_id, agent_id, task_type, message):
            return OntologyAnswer(
                answer={"summary": "图谱回答", "sections": []},
                sources=[],
                graph_evidence=GraphEvidence(ontology_intent="entity_info", confidence=0.9),
            )

    monkeypatch.setattr("sales_agent.services.chat_pipeline.TenantResolver", FakeTenantResolver)
    monkeypatch.setattr("sales_agent.services.chat_pipeline.OntologyAnswerService", FakeOntologyAnswerService, raising=False)

    settings = Settings(
        ontology={"knowledge_engine": "ontology_neo4j"},
        neo4j={"uri": "bolt://fake", "user": "neo4j", "password": "pw"},
    )
    pipeline = ChatPipeline(db_session, settings)
    result = await pipeline.execute(
        tenant_id=sample_tenant,
        user_id="u1",
        message="我们产品的优势是什么",
        conversation_id="conv_ontology_1",
        agent_id=None,
    )

    assert result.answer_dict["summary"] == "图谱回答"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/integration/test_ontology_chat_pipeline.py -v
```

Expected: FAIL because `ChatPipeline` does not call `OntologyAnswerService`.

- [ ] **Step 3: Add factory function in ChatPipeline**

In `src/sales_agent/services/chat_pipeline.py`, import near top:

```python
from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.repository import OntologyRepository
from sales_agent.ontology.retrieval_service import OntologyRetrievalService
```

Add helper near class definitions:

```python
def _build_ontology_answer_service(settings: Settings, model_provider) -> OntologyAnswerService:
    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)
    retrieval = OntologyRetrievalService(repository, model_provider.embedding)
    return OntologyAnswerService(retrieval, model_provider.chat)
```

- [ ] **Step 4: Replace retrieval branch conditionally**

Inside `ChatPipeline.execute`, in the `if path_result.needs_retrieval:` block, branch before legacy `Retriever`:

```python
if path_result.needs_retrieval and self.settings.ontology.knowledge_engine == "ontology_neo4j":
    timings.start("ontology_answer")
    ontology_answer_service = _build_ontology_answer_service(self.settings, model_provider)
    ontology_result = await ontology_answer_service.answer_for_task(
        tenant_id=tenant_id,
        agent_id=resolved_agent_id,
        task_type=task_type,
        message=message,
    )
    answer_dict = ontology_result.answer
    sources = ontology_result.sources
    retrieval_info = {
        "called": True,
        "provider": "ontology_neo4j",
        "graph_evidence": ontology_result.graph_evidence.to_dict(),
    }
    timings.end("ontology_answer")
    await tracer.record_step(
        "ontology_answer",
        latency_ms=int(timings.stages.get("ontology_answer", 0)),
        metadata=retrieval_info,
    )
    retrieval_result = None
    skip_generation = True
else:
    skip_generation = False
```

Then wrap the existing generation section:

```python
if not skip_generation:
    # existing execute_agent generation block remains here
```

Keep risk checking, logging, stats, and response unchanged after generation. Ensure `retrieval_info` flows into `conversation_logger.log_conversation`.

- [ ] **Step 5: Run ontology pipeline test**

Run:

```bash
PYTHONPATH=src pytest tests/integration/test_ontology_chat_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 6: Run related existing tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_processing_notice.py tests/unit/test_task_router.py tests/integration/coach/test_coach_pipeline_integration.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/services/chat_pipeline.py tests/integration/test_ontology_chat_pipeline.py
git commit -m "feat: route retrieval to ontology engine"
```

---

### Task 11: Ontology API Routes And Readiness

**Files:**
- Create: `src/sales_agent/api/routes/ontology.py`
- Modify: `src/sales_agent/main.py`
- Modify: `src/sales_agent/api/routes/health.py`
- Test: `tests/integration/test_ontology_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/integration/test_ontology_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/integration/test_ontology_api.py -v
```

Expected: FAIL because router does not exist.

- [ ] **Step 3: Implement ontology router**

Create `src/sales_agent/api/routes/ontology.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.services.agent_service import AgentService, AgentNotFoundError

router = APIRouter(prefix="/agents", tags=["ontology"])


async def _load_agent_or_404(agent_id: str, db: DbSession):
    try:
        return await AgentService(db).get_agent(agent_id)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/ontology/status")
async def get_ontology_status(agent_id: str, db: DbSession):
    await _load_agent_or_404(agent_id, db)
    settings = get_settings()
    client = Neo4jClient(settings.neo4j)
    ready = False
    if client.enabled:
        ready, _detail = await client.verify_connectivity()
        await client.close()
    return {
        "knowledge_engine": settings.ontology.knowledge_engine,
        "ontology_status": "ready" if ready else ("not_configured" if not client.enabled else "failed"),
        "neo4j_configured": client.enabled,
        "neo4j_ready": ready,
        "visual_url": settings.neo4j.visual_url,
    }


@router.post("/{agent_id}/ontology/ingest", status_code=202)
async def start_ontology_ingest(agent_id: str, body: dict, db: DbSession):
    agent = await _load_agent_or_404(agent_id, db)
    path = body.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    job = IngestionJob(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        engine="ontology_neo4j",
        status="queued",
        stage="queued",
        metadata_json=json.dumps({"path": path}, ensure_ascii=False),
    )
    db.add(job)
    await db.flush()
    return _job_to_dict(job)


@router.get("/{agent_id}/ontology/jobs")
async def list_ontology_jobs(agent_id: str, db: DbSession, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    agent = await _load_agent_or_404(agent_id, db)
    conds = [IngestionJob.tenant_id == agent.tenant_id, IngestionJob.agent_id == agent.id, IngestionJob.engine == "ontology_neo4j"]
    total = (await db.execute(select(func.count()).select_from(IngestionJob).where(*conds))).scalar() or 0
    rows = (await db.execute(
        select(IngestionJob).where(*conds).order_by(IngestionJob.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return {"items": [_job_to_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _job_to_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "agent_id": job.agent_id,
        "engine": job.engine,
        "status": job.status,
        "stage": job.stage,
        "documents_seen": job.documents_seen,
        "documents_ingested": job.documents_ingested,
        "entities_created": job.entities_created,
        "entities_merged": job.entities_merged,
        "facts_created": job.facts_created,
        "facts_active": job.facts_active,
        "facts_pending_review": job.facts_pending_review,
        "facts_rejected": job.facts_rejected,
        "conflicts_created": job.conflicts_created,
        "warnings": json.loads(job.warnings_json or "[]"),
        "errors": json.loads(job.errors_json or "[]"),
        "error_summary": job.error_summary,
        "metadata": json.loads(job.metadata_json or "{}"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
```

This first API creates queued jobs and lists them. Wire actual execution in a later task or worker once the UI and service are ready.

- [ ] **Step 4: Register router**

In `src/sales_agent/main.py`, add `ontology` to route imports and include:

```python
from sales_agent.api.routes import ..., ontology
...
app.include_router(ontology.router)
```

- [ ] **Step 5: Extend readiness**

In `src/sales_agent/api/routes/health.py`, add Neo4j status to `/ready` response when `KNOWLEDGE_ENGINE=ontology_neo4j`. Keep legacy ready behavior unchanged when engine is `legacy_rag`.

Use this shape:

```python
ready_detail["ontology"] = {
    "knowledge_engine": settings.ontology.knowledge_engine,
    "neo4j_configured": bool(settings.neo4j.uri),
    "neo4j_ready": neo4j_ready,
}
```

- [ ] **Step 6: Run tests**

Run:

```bash
PYTHONPATH=src pytest tests/integration/test_ontology_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/api/routes/ontology.py src/sales_agent/main.py src/sales_agent/api/routes/health.py tests/integration/test_ontology_api.py
git commit -m "feat: add ontology management api"
```

---

### Task 12: Console API Wrappers And Knowledge Page

**Files:**
- Modify: `console/src/api/types.ts`
- Modify: `console/src/api/knowledge.ts`
- Modify: `console/src/pages/Agents/AgentKnowledgePage.tsx`
- Create: `console/src/tests/api/knowledge.test.ts`

- [ ] **Step 1: Write failing frontend API tests**

Create `console/src/tests/api/knowledge.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';

// @ts-expect-error - stubbing browser globals in node
globalThis.window = { location: { origin: 'http://localhost' } };

import {
  getOntologyStatus,
  startOntologyIngest,
  listOntologyJobs,
} from '@/api/knowledge';

let lastCall: { method: string; url: string; body: unknown } | null = null;

beforeEach(() => {
  lastCall = null;
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === 'string' ? input : input.toString();
    lastCall = {
      method: init?.method ?? 'GET',
      url: u,
      body: init?.body ? JSON.parse(init.body as string) : null,
    };
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
});

describe('Ontology knowledge API wrappers', () => {
  it('gets ontology status', async () => {
    await getOntologyStatus('a1');
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/ontology/status');
  });

  it('starts ontology ingest', async () => {
    await startOntologyIngest('a1', '/tmp/sample.md');
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents/a1/ontology/ingest');
    expect(lastCall!.body).toEqual({ path: '/tmp/sample.md' });
  });

  it('lists jobs', async () => {
    await listOntologyJobs('a1', 20, 0);
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/ontology/jobs');
    expect(lastCall!.url).toContain('limit=20');
  });
});
```

- [ ] **Step 2: Run frontend test to verify it fails**

Run:

```bash
cd console && npm test -- src/tests/api/knowledge.test.ts
```

Expected: FAIL because wrappers are missing.

- [ ] **Step 3: Add types**

In `console/src/api/types.ts`, add:

```ts
export interface OntologyStatus {
  knowledge_engine: string;
  ontology_status: 'not_configured' | 'ready' | 'degraded' | 'failed';
  neo4j_configured: boolean;
  neo4j_ready: boolean;
  visual_url: string;
}

export interface OntologyJob {
  id: string;
  tenant_id: string;
  agent_id: string | null;
  engine: string;
  status: string;
  stage: string;
  documents_seen: number;
  documents_ingested: number;
  entities_created: number;
  entities_merged: number;
  facts_created: number;
  facts_active: number;
  facts_pending_review: number;
  facts_rejected: number;
  conflicts_created: number;
  warnings: string[];
  errors: Record<string, unknown>[];
  error_summary: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 4: Add API wrappers**

In `console/src/api/knowledge.ts`, add:

```ts
import type { OntologyJob, OntologyStatus, PaginatedResponse } from './types';

export function getOntologyStatus(agentId: string) {
  return apiGet<OntologyStatus>(`/agents/${agentId}/ontology/status`);
}

export function startOntologyIngest(agentId: string, path: string) {
  return apiPost<OntologyJob>(`/agents/${agentId}/ontology/ingest`, { path });
}

export function listOntologyJobs(agentId: string, limit = 20, offset = 0) {
  return apiGet<PaginatedResponse<OntologyJob>>(`/agents/${agentId}/ontology/jobs`, { limit, offset });
}
```

Keep existing tenant knowledge wrappers for backward compatibility.

- [ ] **Step 5: Update Agent knowledge page**

Modify `console/src/pages/Agents/AgentKnowledgePage.tsx` to add:

- Query `getOntologyStatus(agentId)`.
- Query `listOntologyJobs(agentId)`.
- A path input and "启动 Neo4j 入库" button calling `startOntologyIngest`.
- Cards for status, progress stats, conflicts.
- Button/link to `status.visual_url` when present.

Use this compact structure:

```tsx
const [path, setPath] = useState('');
const queryClient = useQueryClient();
const statusQuery = useQuery({ queryKey: ['ontology-status', agentId], queryFn: () => getOntologyStatus(agentId!), enabled: !!agentId });
const jobsQuery = useQuery({ queryKey: ['ontology-jobs', agentId], queryFn: () => listOntologyJobs(agentId!, 20, 0), enabled: !!agentId });
const ingestMutation = useMutation({
  mutationFn: () => startOntologyIngest(agentId!, path),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ontology-jobs', agentId] }),
});
```

Render status tags:

```tsx
<Tag color={statusQuery.data?.neo4j_ready ? 'green' : 'orange'}>
  {statusQuery.data?.ontology_status ?? 'unknown'}
</Tag>
```

Render job table columns:

```tsx
{ title: '阶段', dataIndex: 'stage', key: 'stage' }
{ title: '实体', dataIndex: 'entities_created', key: 'entities_created' }
{ title: 'Fact', dataIndex: 'facts_created', key: 'facts_created' }
{ title: '待复核', dataIndex: 'facts_pending_review', key: 'facts_pending_review' }
{ title: '冲突', dataIndex: 'conflicts_created', key: 'conflicts_created' }
```

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd console && npm test -- src/tests/api/knowledge.test.ts
cd console && npm run build
```

Expected: tests PASS and build succeeds.

- [ ] **Step 7: Commit**

```bash
git add console/src/api/types.ts console/src/api/knowledge.ts console/src/pages/Agents/AgentKnowledgePage.tsx console/src/tests/api/knowledge.test.ts
git commit -m "feat: add ontology ingestion console"
```

---

### Task 13: End-To-End Verification With Fakes And Full Test Sweep

**Files:**
- Create: `tests/integration/test_ontology_end_to_end_fake.py`
- Modify: no production files unless this task exposes bugs.

- [ ] **Step 1: Write fake end-to-end test**

Create `tests/integration/test_ontology_end_to_end_fake.py`:

```python
import pytest

from sales_agent.ontology.ingestion_service import OntologyIngestionService
from sales_agent.ontology.retrieval_service import OntologyRetrievalService
from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate


class FakeExtractor:
    async def extract_entities(self, content):
        return [EntityCandidate(type="Product", name="福利卡", properties={"定位": "员工福利"})]

    async def extract_facts(self, content, entities):
        return [FactCandidate(subject_name="福利卡", predicate="description", value="员工福利产品", fact_type="attribute")]


class FakeEmbedding:
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class MemoryRepository:
    def __init__(self):
        self.entities = []
        self.facts = []

    async def upsert_entity(self, params):
        self.entities.append(params)
        return params["id"], True

    async def create_fact(self, params):
        self.facts.append(params)
        return params["fact_id"]

    async def retrieve_by_query(self, params):
        return [{
            "e": {"id": self.entities[0]["id"], "name": self.entities[0]["name"], "type": self.entities[0]["type"]},
            "f": {"id": self.facts[0]["fact_id"], "predicate": self.facts[0]["predicate"], "value": self.facts[0]["value"]},
            "o": None,
            "evidence": [{"excerpt": self.facts[0]["excerpt"]}],
            "documents": [{"id": self.facts[0]["source_document_id"], "title": self.facts[0]["source_title"]}],
        }]

    async def query_vector(self, params):
        return []


class FakeChat:
    async def generate(self, messages, temperature=0.2, max_tokens=1600):
        return '{"answer":"福利卡是员工福利产品。","evidence":["description"],"confidence":0.9}'


@pytest.mark.asyncio
async def test_fake_ingest_then_retrieve_answer(tmp_path, db_session, sample_tenant):
    path = tmp_path / "sample.md"
    path.write_text("福利卡是员工福利产品。", encoding="utf-8")
    repo = MemoryRepository()
    ingestion = OntologyIngestionService(db_session, repo, FakeEmbedding(), FakeExtractor())
    job, stats = await ingestion.ingest_paths(tenant_id=sample_tenant, agent_id="a1", paths=[path])
    assert job.status == "completed"
    assert stats.entities_created == 1
    assert stats.facts_created == 1

    retrieval = OntologyRetrievalService(repo, FakeEmbedding())
    answer_service = OntologyAnswerService(retrieval, FakeChat())
    answer = await answer_service.answer_for_task(
        tenant_id=sample_tenant,
        agent_id="a1",
        task_type="knowledge_qa",
        message="福利卡是什么",
    )
    assert answer.answer["summary"] == "福利卡是员工福利产品。"
    assert answer.graph_evidence.source_documents[0]["title"] == "sample.md"
```

- [ ] **Step 2: Run fake end-to-end test**

Run:

```bash
PYTHONPATH=src pytest tests/integration/test_ontology_end_to_end_fake.py -v
```

Expected: PASS.

- [ ] **Step 3: Run backend related suite**

Run:

```bash
PYTHONPATH=src pytest tests/unit/ontology tests/unit/test_ontology_config.py tests/unit/test_ingestion_job.py tests/integration/test_ontology_api.py tests/integration/test_ontology_chat_pipeline.py tests/integration/test_ontology_end_to_end_fake.py -v
```

Expected: PASS.

- [ ] **Step 4: Run existing high-risk regression tests**

Run:

```bash
PYTHONPATH=src pytest tests/unit/test_task_router.py tests/unit/test_risk_checker.py tests/unit/test_processing_notice.py tests/integration/coach/test_coach_pipeline_integration.py -v
```

Expected: PASS.

- [ ] **Step 5: Run frontend related checks**

Run:

```bash
cd console && npm test -- src/tests/api/knowledge.test.ts src/tests/api/agents.test.ts
cd console && npm run build
```

Expected: PASS and build succeeds.

- [ ] **Step 6: Commit test hardening**

```bash
git add tests/integration/test_ontology_end_to_end_fake.py
git commit -m "test: cover ontology fake end to end"
```

---

### Task 14: Documentation And Operator Notes

**Files:**
- Modify: `README.md`
- Create: `docs/ontology-neo4j-ops.md`
- Modify: `docker-compose.yml` only if adding optional local Neo4j service.

- [ ] **Step 1: Add operator documentation**

Create `docs/ontology-neo4j-ops.md`:

```markdown
# Neo4j Ontology Knowledge Engine Operations

## Enable

Set:

```dotenv
KNOWLEDGE_ENGINE=ontology_neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=neo4j
NEO4J_VISUAL_URL=http://localhost:7474
ONTOLOGY_VECTOR_FALLBACK=conservative
```

## First Validation

1. Start Postgres as usual.
2. Start Neo4j separately or through local compose if configured.
3. Run the app.
4. Open `/agents/{agent_id}/knowledge`.
5. Check ontology status.
6. Start an ingest job with a small Markdown sample.
7. Ask a `knowledge_qa` question.

## Expected Behavior

- Neo4j stores Entity, Fact, Evidence, and SourceDocument nodes.
- PostgreSQL stores ingestion jobs and chat logs.
- Chat responses keep `summary/sections`.
- Trace metadata includes `graph_evidence`.
- Pending review facts do not support user-facing answers.
```

- [ ] **Step 2: Update README**

Add a short section to `README.md` under RAG or knowledge base:

```markdown
### Neo4j Ontology Knowledge Engine

The app can replace legacy Markdown chunk RAG with a Neo4j-backed ontology engine.
Set `KNOWLEDGE_ENGINE=ontology_neo4j` and configure `NEO4J_*` environment variables.
The first version uses graph retrieval with conservative entity-vector fallback and keeps
chat output compatible with existing `summary/sections` responses.

See `docs/ontology-neo4j-ops.md` for operator steps.
```

- [ ] **Step 3: Run documentation smoke checks**

Run:

```bash
grep -n "Neo4j Ontology" README.md docs/ontology-neo4j-ops.md
```

Expected: both files contain the section.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ontology-neo4j-ops.md
git commit -m "docs: add neo4j ontology operations"
```

---

## Final Verification

- [ ] **Step 1: Run backend ontology suite**

```bash
PYTHONPATH=src pytest tests/unit/ontology tests/integration/test_ontology_api.py tests/integration/test_ontology_chat_pipeline.py tests/integration/test_ontology_end_to_end_fake.py -v
```

Expected: PASS.

- [ ] **Step 2: Run broader backend regression**

```bash
PYTHONPATH=src pytest tests/unit/test_task_router.py tests/unit/test_risk_checker.py tests/unit/test_processing_notice.py tests/unit/test_prompt_registry.py tests/integration/coach/test_coach_pipeline_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend checks**

```bash
cd console && npm test -- src/tests/api/knowledge.test.ts src/tests/api/agents.test.ts
cd console && npm run build
```

Expected: PASS and build succeeds.

- [ ] **Step 4: Inspect git status**

```bash
git status --short
```

Expected: only pre-existing unrelated files remain, or clean if those were handled separately. Do not revert pre-existing unrelated changes.

## Self-Review Notes

Spec coverage:

- Neo4j storage and Fact model: Tasks 3 and 4.
- PostgreSQL metadata: Task 7.
- Automatic extraction: Tasks 5 and 8.
- Conservative vector fallback: Task 9.
- Runtime RAG replacement: Task 10.
- Console ingestion visualization and Neo4j visual URL: Task 12.
- Readiness and error visibility: Task 11.
- Tests and acceptance: Task 13.
- Operator docs: Task 14.

No task implements Agentic planner, full taishan migration, Ontology-minimal data migration, or custom graph visualization, matching the approved non-goals.
