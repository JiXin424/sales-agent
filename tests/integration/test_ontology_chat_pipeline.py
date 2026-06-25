import pytest

from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer


@pytest.mark.asyncio
async def test_chat_pipeline_uses_ontology_when_configured(monkeypatch, db_session, sample_tenant):
    from sales_agent.core.config import Settings
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
