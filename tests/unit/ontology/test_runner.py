import pytest
from sales_agent.ontology.runner import LLMExtractor, build_ingestion_service


class FakeChatModel:
    async def generate(self, messages, temperature=None, max_tokens=None, **kwargs):
        return '{"entities":[{"type":"Product","name":"福利卡"},{"type":"Concept","name":"福多多"}]}'


class FakeEmbeddingModel:
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class FakeModelProvider:
    def __init__(self):
        self.chat = FakeChatModel()
        self.embedding = FakeEmbeddingModel()


async def test_llm_extractor_returns_entities():
    extractor = LLMExtractor(FakeChatModel())
    entities = await extractor.extract_entities("福多多提供员工福利产品。")
    assert len(entities) >= 1
    assert entities[0].type == "Product"
    assert entities[0].name == "福利卡"
    facts = await extractor.extract_facts("test content", entities)
    assert isinstance(facts, list)


@pytest.mark.asyncio
async def test_build_ingestion_service_returns_service(db_session):
    from sales_agent.core.config import Settings
    settings = Settings(
        ontology={"knowledge_engine": "ontology_neo4j"},
        neo4j={"uri": "bolt://fake", "user": "neo4j", "password": "pw"},
    )
    service = build_ingestion_service(db_session, settings, FakeModelProvider())
    assert service is not None
