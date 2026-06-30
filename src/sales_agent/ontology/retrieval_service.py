from __future__ import annotations

import re
import time
from typing import Protocol

from sales_agent.llm.base import EmbeddingModel
from sales_agent.ontology.schemas import GraphEvidence

# ── 关键词提取：把自然语言问句转为实体搜索词 ──────────────────────────
# 中文问句包含大量功能词（"包含什么""有哪些""是什么"等），直接用作
# Cypher CONTAINS 子串匹配几乎不可能命中实体名。这里用正则剥离疑问/
# 功能词，将剩余实词作为搜索词数组传入 Neo4j，配合 any(term IN ...)
# 实现多词 OR 匹配。
_QUESTION_STRIP_RE = re.compile(
    r"包含什么|有哪些|是什么|什么样|怎么样|如何|"
    r"为什么|会不会|是不是|能不能|有没有|有什么|"
    r"请告诉|请问|麻烦|帮我|"
    r"[什么哪些怎几]|"
    r"[的吗呢吧啊呀][？?]?$|"
    r"[的了么着过]"
)


def _extract_search_terms(question: str) -> list[str]:
    """从自然语言问句中提取实体搜索关键词。

    去除疑问词和功能词后，按空白/标点切分，返回 >=2 字符的独立词条。
    若提取后无有效词条，返回原始问题作为兜底。
    """
    text = question.rstrip("？?!!。").strip()
    text = _QUESTION_STRIP_RE.sub(" ", text)
    terms = [t.strip() for t in re.split(r"[\s,，、。．.；;：:和与及]+", text) if len(t.strip()) >= 2]
    return list(dict.fromkeys(terms)) if terms else [question]


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
        search_terms = _extract_search_terms(question)
        rows = await self.repository.retrieve_by_query({
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "search_terms": search_terms,
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
            for row in vector_rows:
                if row.get("e"):
                    matched_entities.append(self._node(row.get("e")))
                for f in (row.get("facts") or []):
                    if f:
                        facts.append(self._node(f))
                for ev in (row.get("evidence") or []):
                    if ev:
                        evidence.append(self._node(ev))
                for d in (row.get("documents") or []):
                    if d:
                        documents.append(self._node(d))

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
