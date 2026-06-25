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
        return [{
            "e": {"id": "vec1", "name": "向量实体", "type": "Product"},
            "score": 0.91,
            "facts": [{"id": "f1", "predicate": "description", "value": "员工福利"}],
            "evidence": [{"excerpt": "原文片段"}],
            "documents": [{"id": "d1", "title": "sample.md"}],
        }]


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
    # 向量回退也必须带回来源（facts/evidence/documents），不能只返回实体
    assert len(evidence.matched_entities) == 1
    assert any(f.get("predicate") == "description" for f in evidence.facts_used)
    assert evidence.source_documents[0]["title"] == "sample.md"
