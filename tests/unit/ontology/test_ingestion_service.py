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
