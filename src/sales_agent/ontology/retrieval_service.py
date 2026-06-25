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
