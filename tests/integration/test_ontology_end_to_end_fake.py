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
