from __future__ import annotations

import json
import logging
import time
from typing import Protocol

from sales_agent.llm.base import ChatModel, EmbeddingModel
from sales_agent.ontology.schemas import GraphEvidence

logger = logging.getLogger(__name__)

# ── LLM 实体关键词提取 prompt ──────────────────────────────────────────
# 用极轻量 LLM 调用（temperature=0, max_tokens=100）从自然语言问句中
# 抽取实体名/关键词，替代正则穷举。LLM 天然理解任意问句结构，无需维护词表。
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""


class RepositoryProtocol(Protocol):
    async def retrieve_by_query(self, params: dict) -> list[dict]: ...
    async def query_vector(self, params: dict) -> list[dict]: ...


class OntologyRetrievalService:
    def __init__(
        self,
        repository: RepositoryProtocol,
        embedding_model: EmbeddingModel,
        chat_model: ChatModel | None = None,
        limit: int = 200,
    ):
        self.repository = repository
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.limit = limit

    async def _extract_search_terms(self, question: str) -> list[str]:
        """用 LLM 从问句中提取实体名/关键词，失败时用原始问题兜底。"""
        if self.chat_model is None:
            logger.info("Ontology entity extraction: no chat_model, using raw question as search term")
            return [question]

        try:
            raw = await self.chat_model.generate(
                messages=[{
                    "role": "user",
                    "content": _ENTITY_EXTRACTION_PROMPT.format(question=question),
                }],
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(raw)
            # 兼容各种 LLM 输出格式：直接的数组、或包裹在 key 里的
            if isinstance(parsed, list):
                terms = [str(t).strip() for t in parsed if str(t).strip()]
            elif isinstance(parsed, dict):
                # 取第一个值是 list 的 key，否则取第一个非空字符串值
                for val in parsed.values():
                    if isinstance(val, list):
                        terms = [str(t).strip() for t in val if str(t).strip()]
                        break
                else:
                    terms = [str(v).strip() for v in parsed.values() if str(v).strip()]
            else:
                terms = [question]
            terms = list(dict.fromkeys(terms)) if terms else [question]
            logger.info(
                "Ontology entity extraction: question=%r → terms=%s",
                question[:120], terms,
            )
            return terms
        except Exception:
            logger.warning("LLM entity extraction failed, using raw question", exc_info=True)
            return [question]

    async def retrieve(self, *, tenant_id: str, agent_id: str | None, question: str) -> GraphEvidence:
        started = time.monotonic()
        search_terms = await self._extract_search_terms(question)
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
            logger.info(
                "Ontology graph query returned 0 entities for terms=%s, trying vector fallback",
                search_terms,
            )
            from sales_agent.core.config import get_settings
            embedding = (await self.embedding_model.embed([question]))[0]
            vector_rows = await self.repository.query_vector({
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "embedding": embedding,
                "limit": get_settings().ontology.vector_fallback_top_k,
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

        # 按搜索词相关性排序事实，确保最相关的事实排在前面被 LLM 看到
        if search_terms and facts:
            def _fact_score(f: dict) -> int:
                fv = str(f.get("value", "")) + str(f.get("predicate", ""))
                return sum(1 for t in search_terms if t in fv)
            facts.sort(key=_fact_score, reverse=True)

        result = GraphEvidence(
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
        logger.info(
            "Ontology retrieval result: strategy=%s entities=%d facts=%d docs=%d confidence=%.2f latency_ms=%d",
            result.retrieval_strategy, len(result.matched_entities),
            len(result.facts_used), len(result.source_documents),
            result.confidence, result.timings_ms.get("ontology_retrieval", 0),
        )
        if result.matched_entities:
            entity_summary = [
                f"{e.get('name', '?')}({e.get('type', '?')})"
                for e in result.matched_entities[:10]
            ]
            logger.info("Ontology matched entities: %s", entity_summary)
        return result

    def _node(self, value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return dict(value)
        except Exception:  # noqa: BLE001
            return {"value": str(value)}
