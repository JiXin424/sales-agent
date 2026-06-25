"""Live Neo4j integration tests for the ontology engine.

These hit a REAL Neo4j (verifies the Cypher in repository.py, the vector index,
and the schema bootstrap that the unit tests only cover with fakes).

Gated: only runs when NEO4J_LIVE_TEST is set, e.g.:
    NEO4J_LIVE_TEST=1 .venv/bin/pytest tests/integration/test_ontology_neo4j_live.py -v

Defaults point at the local docker-compose neo4j service
(bolt://localhost:7687, neo4j/neo4jtest123). Each test uses a unique tenant_id and
cleans up its nodes afterwards, so it is safe to re-run.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_LIVE_TEST"),
    reason="set NEO4J_LIVE_TEST=1 (and NEO4J_URI/USER/PASSWORD if non-default) to run live Neo4j tests",
)

from sales_agent.core.config import Neo4jConfig  # noqa: E402
from sales_agent.ontology.answer_service import OntologyAnswerService  # noqa: E402
from sales_agent.ontology.ingestion_service import OntologyIngestionService  # noqa: E402
from sales_agent.ontology.neo4j_client import Neo4jClient  # noqa: E402
from sales_agent.ontology.repository import OntologyRepository  # noqa: E402
from sales_agent.ontology.retrieval_service import OntologyRetrievalService  # noqa: E402
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate  # noqa: E402
from sales_agent.ontology.schema import ensure_ontology_schema  # noqa: E402


def _config() -> Neo4jConfig:
    return Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "neo4jtest123"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )


class FakeExtractor:
    async def extract_entities(self, content):
        return [EntityCandidate(type="Product", name="福利卡", properties={"定位": "员工福利"})]

    async def extract_facts(self, content, entities):
        return [FactCandidate(subject_name="福利卡", predicate="description", value="员工福利产品", fact_type="attribute")]


class FakeEmbedding:
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class FakeChat:
    async def generate(self, messages, temperature=0.2, max_tokens=1600, **kwargs):
        return '{"answer":"福利卡是员工福利产品。","evidence":["description"],"confidence":0.9}'


async def _cleanup(client: Neo4jClient, tenant: str) -> None:
    async with client.session() as s:
        await s.run("MATCH (n) WHERE n.tenant_id = $t DETACH DELETE n", t=tenant)


@pytest.mark.asyncio
async def test_live_schema_bootstrap_creates_indexes_and_constraints():
    """The schema bootstrap that main.py lifespan calls must actually create the
    vector index + uniqueness constraint on real Neo4j."""
    client = Neo4jClient(_config())
    try:
        await ensure_ontology_schema(client)  # idempotent; must not raise

        async with client.session() as s:
            idx_row = await (await s.run("SHOW INDEXES YIELD name RETURN collect(name) AS names")).single()
            con_row = await (await s.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")).single()
        idx_names = idx_row["names"] if idx_row else []
        con_names = con_row["names"] if con_row else []

        assert "entity_embedding_vector" in idx_names, f"vector index missing; got {idx_names}"
        assert "entity_name_fulltext" in idx_names
        assert "entity_canonical_unique" in con_names, f"unique constraint missing; got {con_names}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_ingest_then_retrieve_and_answer(tmp_path, db_session):
    """Full real path: PG job + real Neo4j graph (MERGE entity, CREATE fact) +
    real graph retrieval + answer. This is the path the unit tests only faked."""
    tenant = f"live_{uuid.uuid4().hex[:8]}"
    client = Neo4jClient(_config())
    repo = OntologyRepository(client)
    try:
        path = tmp_path / "sample.md"
        path.write_text("# 福利卡\n福多多提供员工福利产品。", encoding="utf-8")

        job, stats = await OntologyIngestionService(
            db_session, repo, FakeEmbedding(), FakeExtractor()
        ).ingest_paths(tenant_id=tenant, agent_id="agent1", paths=[path])

        assert job.status == "completed"
        assert stats.entities_created == 1
        assert stats.facts_created == 1

        retrieval = OntologyRetrievalService(repo, FakeEmbedding())
        evidence = await retrieval.retrieve(tenant_id=tenant, agent_id="agent1", question="福利卡")
        assert len(evidence.matched_entities) >= 1, "graph retrieval should find the ingested entity"
        assert evidence.vector_fallback_used is False, "graph matched → no vector fallback"
        assert any(f.get("predicate") == "description" for f in evidence.facts_used)

        answer = await OntologyAnswerService(retrieval, FakeChat()).answer_for_task(
            tenant_id=tenant, agent_id="agent1", task_type="knowledge_qa", message="福利卡是什么"
        )
        assert answer.answer["summary"] == "福利卡是员工福利产品。"
        assert answer.graph_evidence.source_documents[0]["title"] == "sample.md"
    finally:
        await _cleanup(client, tenant)
        await client.close()


@pytest.mark.asyncio
async def test_live_vector_fallback_when_graph_misses(tmp_path, db_session):
    """Conservative fallback: a query the graph can't match must fall through to the
    real vector index query and still surface the ingested entity."""
    tenant = f"live_{uuid.uuid4().hex[:8]}"
    client = Neo4jClient(_config())
    repo = OntologyRepository(client)
    try:
        path = tmp_path / "sample.md"
        path.write_text("福利卡是员工福利产品。", encoding="utf-8")
        await OntologyIngestionService(
            db_session, repo, FakeEmbedding(), FakeExtractor()
        ).ingest_paths(tenant_id=tenant, agent_id="agent1", paths=[path])

        # vector index refreshes asynchronously; give it a moment
        time.sleep(2.0)

        retrieval = OntologyRetrievalService(repo, FakeEmbedding())
        evidence = await retrieval.retrieve(
            tenant_id=tenant, agent_id="agent1", question="完全不存在的关键词xyz"
        )
        assert evidence.vector_fallback_used is True, "graph miss must trigger vector fallback"
        assert len(evidence.matched_entities) >= 1, "vector fallback should surface the entity"
        assert evidence.retrieval_strategy == "graph_vector_fallback"
    finally:
        await _cleanup(client, tenant)
        await client.close()


LIVE_LLM = os.getenv("ONTOLOGY_LIVE_LLM")


@pytest.mark.skipif(not LIVE_LLM, reason="set ONTOLOGY_LIVE_LLM=1 to test real LLM (DeepSeek + dashscope)")
@pytest.mark.asyncio
async def test_live_real_llm_ingest_retrieve(tmp_path, db_session):
    """真实 LLM 抽取 + embedding：验证 DeepSeek JSON 稳定性和 dashscope 1024 维向量。"""
    tenant = f"livellm_{uuid.uuid4().hex[:8]}"
    settings = __import__('sales_agent.core.config', fromlist=['get_settings']).get_settings()

    from sales_agent.services.tenant_resolver import TenantResolver
    from sales_agent.ontology.runner import build_ingestion_service
    from sales_agent.ontology.retrieval_service import OntologyRetrievalService
    from sales_agent.ontology.answer_service import OntologyAnswerService
    from sales_agent.ontology.repository import OntologyRepository

    # 真实 LLM provider — 走 TenantResolver（需要 PG db_session）
    resolver = TenantResolver(db_session)
    tenant_info = await resolver.resolve(tenant)
    provider = resolver.get_model_provider(tenant_info)

    # 验证 embedding 维度 = 1024
    embeds = await provider.embedding.embed(["测试"])
    assert len(embeds) == 1
    assert len(embeds[0]) == 1024, f"embedding dim {len(embeds[0])}, expected 1024"

    neo_client = Neo4jClient(settings.neo4j)
    try:
        path = tmp_path / "sample.md"
        path.write_text(
            "# 福多多产品线\n"
            "福多多提供员工福利卡、年节礼包和企业下午茶服务。"
            "价格方面，福利卡面额100-500元，企业下午茶人均30-80元。",
            encoding="utf-8",
        )

        service = build_ingestion_service(db_session, settings, provider)
        job, stats = await service.ingest_paths(
            tenant_id=tenant, agent_id="agent1", paths=[path],
        )
        assert job.status in ("completed", "completed_with_errors")
        assert stats.entities_created >= 1, f"LLM should extract >=1 entity; got {stats.entities_created}"
        assert stats.facts_created >= 1, f"LLM should extract >=1 fact; got {stats.facts_created}"

        # 验证实体和事实能从 neo4j 读出
        repo = OntologyRepository(neo_client)
        retrieval = OntologyRetrievalService(repo, provider.embedding)
        evidence = await retrieval.retrieve(tenant_id=tenant, agent_id="agent1", question="福多多产品")
        assert len(evidence.matched_entities) >= 1

        # 回答
        answer_service = OntologyAnswerService(retrieval, provider.chat)
        answer = await answer_service.answer_for_task(
            tenant_id=tenant, agent_id="agent1", task_type="knowledge_qa", message="福多多有什么产品"
        )
        assert len(answer.answer["summary"]) > 10  # 真实 LLM 回答必须有内容
    finally:
        async with neo_client.session() as s:
            await s.run("MATCH (n) WHERE n.tenant_id = $t DETACH DELETE n", t=tenant)
        await neo_client.close()


@pytest.mark.asyncio
async def test_live_docx_file_ingest(tmp_path, db_session):
    """真实 .docx 文件 → _read_content(python-docx) → ingest → retrieve 端到端。
    验证 docx 二进制能被解析成文本并走通图谱入库（用 FakeExtractor 隔离 LLM）。"""
    try:
        from docx import Document as DocxDoc
    except ImportError:
        pytest.skip("python-docx 未安装")

    tenant = f"livedocx_{uuid.uuid4().hex[:8]}"
    client = Neo4jClient(_config())
    repo = OntologyRepository(client)
    try:
        path = tmp_path / "sample.docx"
        doc = DocxDoc()
        doc.add_paragraph("福多多提供员工福利产品。")
        doc.save(str(path))

        job, stats = await OntologyIngestionService(
            db_session, repo, FakeEmbedding(), FakeExtractor()
        ).ingest_paths(tenant_id=tenant, agent_id="agent1", paths=[path])

        assert job.status == "completed"
        assert stats.entities_created == 1
        assert stats.facts_created == 1

        retrieval = OntologyRetrievalService(repo, FakeEmbedding())
        evidence = await retrieval.retrieve(tenant_id=tenant, agent_id="agent1", question="福利卡")
        assert len(evidence.matched_entities) >= 1
    finally:
        await _cleanup(client, tenant)
        await client.close()
