from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import Settings
from sales_agent.llm.base import ChatModel, ModelProvider
from sales_agent.ontology.extractor import extract_entities, extract_facts
from sales_agent.ontology.ingestion_service import OntologyIngestionService
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.repository import OntologyRepository
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate


class LLMExtractor:
    """Adapt ChatModel to the ExtractorProtocol that OntologyIngestionService expects."""

    def __init__(self, chat_model: ChatModel):
        self._chat = chat_model

    async def extract_entities(
        self,
        content: str,
        *,
        db: AsyncSession | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[EntityCandidate]:
        return await extract_entities(
            self._chat, content, db=db, tenant_id=tenant_id, agent_id=agent_id
        )

    async def extract_facts(
        self,
        content: str,
        entities: list[EntityCandidate],
        *,
        db: AsyncSession | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[FactCandidate]:
        return await extract_facts(
            self._chat, content, entities, db=db, tenant_id=tenant_id, agent_id=agent_id
        )


def build_ingestion_service(
    db: AsyncSession,
    settings: Settings,
    model_provider: ModelProvider,
) -> OntologyIngestionService:
    """Build an OntologyIngestionService wired to real Neo4j + the tenant's LLM provider."""
    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)
    extractor = LLMExtractor(model_provider.chat)
    return OntologyIngestionService(
        db=db,
        repository=repository,
        embedding_model=model_provider.embedding,
        extractor=extractor,
    )
