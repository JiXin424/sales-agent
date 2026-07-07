"""RAG 检索服务：向量检索 + 关键词检索 + RRF 混合融合，强制 tenant_id 过滤。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import get_settings
from sales_agent.llm.base import EmbeddingModel
from sales_agent.models.document import DocumentChunk, Document
from sales_agent.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalSource:
    """检索命中的来源。"""

    chunk_id: str
    document_id: str
    tenant_id: str
    title: str = ""
    section_title: str = ""
    text: str = ""
    score: float = 0.0
    source_type: str = ""
    metadata: dict = field(default_factory=dict)

    def to_source_item(self) -> dict:
        """转换为 API 响应的 source 格式。"""
        return {
            "document_id": self.document_id,
            "title": self.title,
            "section_title": self.section_title,
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "display_title": self.title,
            "snippet_ref": f"{self.document_id}#{self.chunk_id}",
            "snippet_url": None,
            "score": round(self.score, 4),
            "text": (self.text or "")[:2000],
        }


@dataclass
class RetrievalResult:
    """检索结果。"""

    sources: list[RetrievalSource] = field(default_factory=list)
    query: str = ""
    success: bool = True
    degraded: bool = False
    error_message: str = ""
    skip_reason: str = ""  # 跳过原因（spec §6.2）
    retrieval_latency_ms: float = 0.0  # 检索耗时
    trace_hits: list[dict] = field(default_factory=list)  # per-channel ranked hits

    @property
    def has_results(self) -> bool:
        return len(self.sources) > 0


class Retriever:
    """RAG 检索器。"""

    def __init__(self, db: AsyncSession, embedding_model: EmbeddingModel):
        self.db = db
        self.embedding_model = embedding_model
        self.vector_store = VectorStore(db)
        self.settings = get_settings()

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        allowed_document_ids: set[str] | None = None,
        knowledge_version_id: str | None = None,
    ) -> RetrievalResult:
        """执行检索。

        Args:
            tenant_id: 租户 ID（强制过滤）
            query: 检索文本
            top_k: 返回数量，默认从配置读取
            min_score: 最低相似度，默认从配置读取
            allowed_document_ids: Agent 知识作用域允许的 document_id 集合。
                None 表示不限（tenant 全量）；空集合表示 Agent 作用域为空。
            knowledge_version_id: 可选的知识版本 ID，用于多版本隔离。

        Returns:
            RetrievalResult 包含检索来源和状态
        """
        top_k = top_k or self.settings.retrieval.top_k
        min_score = min_score or self.settings.retrieval.min_score

        try:
            # 1. 生成查询向量
            embeddings = await self.embedding_model.embed([query])
            query_embedding = embeddings[0]

            # 2. 向量检索（vector_store.search 已强制 tenant_id 过滤）
            raw_results = await self.vector_store.search(
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                top_k=top_k,
                min_score=min_score,
            )

            # 3. 二次校验：确保所有结果的 tenant_id 一致
            sources = []
            for item in raw_results:
                if item.get("tenant_id") != tenant_id:
                    logger.error(
                        "Cross-tenant leakage detected! Expected %s, got %s. "
                        "chunk_id=%s, document_id=%s",
                        tenant_id,
                        item.get("tenant_id"),
                        item.get("id"),
                        item.get("document_id"),
                    )
                    # 记录风险但不泄露数据
                    continue

                sources.append(
                    RetrievalSource(
                        chunk_id=item.get("id", ""),
                        document_id=item.get("document_id", ""),
                        tenant_id=item["tenant_id"],
                        title=item.get("title", ""),
                        section_title=item.get("section_title", ""),
                        text=item.get("text", ""),
                        score=item.get("score", 0.0),
                        source_type=item.get("metadata", {}).get("source_type", ""),
                        metadata=item.get("metadata", {}),
                    )
                )

            # 4. Agent 知识作用域后过滤（document_subset）
            if allowed_document_ids is not None:
                sources = [
                    s for s in sources if s.document_id in allowed_document_ids
                ]

            return RetrievalResult(
                sources=sources,
                query=query,
                success=True,
                degraded=False,
            )

        except Exception as e:
            logger.error("Retrieval failed for tenant %s: %s", tenant_id, e, exc_info=True)
            return RetrievalResult(
                query=query,
                success=False,
                degraded=True,
                error_message=str(e),
            )

    async def retrieve_for_task(
        self,
        tenant_id: str,
        message: str,
        task_type: str,
        needs_retrieval: bool = True,
        allowed_document_ids: set[str] | None = None,
        knowledge_version_id: str | None = None,
    ) -> RetrievalResult:
        """根据任务类型判断是否需要检索，并执行。

        对应 spec §6 RAG 条件触发。
        """
        # 如果任务不需要检索
        if not needs_retrieval and task_type not in ("knowledge_qa",):
            return RetrievalResult(
                query=message,
                success=True,
                degraded=False,
                skip_reason="task_does_not_need_enterprise_facts",
            )

        # knowledge_qa 必须检索
        if task_type == "knowledge_qa":
            start = __import__("time").monotonic()
            result = await self.retrieve(
                tenant_id, message,
                allowed_document_ids=allowed_document_ids,
                knowledge_version_id=knowledge_version_id,
            )
            result.retrieval_latency_ms = (__import__("time").monotonic() - start) * 1000
            if not result.has_results:
                result.degraded = True
                result.skip_reason = "knowledge_qa_no_results"
            return result

        # 其他任务类型的条件检索
        start = __import__("time").monotonic()
        result = await self.retrieve(
            tenant_id, message,
            allowed_document_ids=allowed_document_ids,
            knowledge_version_id=knowledge_version_id,
        )
        result.retrieval_latency_ms = (__import__("time").monotonic() - start) * 1000
        return result


def _find_rank(chunk_id: str, ranked: list[tuple[str, float]]) -> int | None:
    """Find the 1-indexed rank of a chunk_id in the ranked list."""
    for i, (cid, _) in enumerate(ranked, start=1):
        if cid == chunk_id:
            return i
    return None


# ── Hybrid Retriever (RRF) ─────────────────────────────────────────────


class HybridRetriever:
    """混合检索器：向量检索 + 关键词检索 → RRF 融合排序。

    RRF (Reciprocal Rank Fusion) 将两种检索结果的排名合并为统一分数：

        score(d) = vector_weight / (k + rank_vector)
                 + keyword_weight / (k + rank_keyword)

    其中 k 是常数（默认 60），用于平滑排名差异。

    使用方式：::

        hr = HybridRetriever(vector_retriever=retriever, keyword_retriever=kr)
        result = await hr.retrieve(tenant_id, "客户嫌贵怎么办", top_k=5)
    """

    def __init__(
        self,
        vector_retriever: "Retriever",
        keyword_retriever: Any,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.keyword_retriever = keyword_retriever
        self.settings = get_settings()

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        allowed_document_ids: set[str] | None = None,
        knowledge_version_id: str | None = None,
    ) -> RetrievalResult:
        """执行混合检索（向量 + 关键词 RRF 融合）。

        Args:
            tenant_id: 租户 ID。
            query: 检索查询文本。
            top_k: 最终返回数量。
            min_score: 最低 RRF 分数（暂未对 RRF 分数做阈值过滤）。
            allowed_document_ids: Agent 知识作用域过滤。
            knowledge_version_id: 可选的知识版本 ID，用于多版本隔离。

        Returns:
            :class:`RetrievalResult`
        """
        top_k = top_k or self.settings.retrieval.top_k
        rrf_k = self.settings.retrieval.rrf_k
        keyword_weight = self.settings.retrieval.keyword_weight
        vector_weight = 1.0 - keyword_weight

        # 并行执行两种检索
        import asyncio

        vector_result: RetrievalResult | None = None
        keyword_hits: list[Any] = []

        async def _vector():
            nonlocal vector_result
            vector_result = await self.vector_retriever.retrieve(
                tenant_id, query, top_k=max(top_k * 3, 30),
                min_score=min_score,
                allowed_document_ids=allowed_document_ids,
            )

        async def _keyword():
            nonlocal keyword_hits
            from sales_agent.rag.keyword_retriever import KeywordHit
            keyword_hits = await self.keyword_retriever.search(
                tenant_id, query, top_k=max(top_k * 3, 30),
            )

        await asyncio.gather(_vector(), _keyword())

        # 构建 chunk_id → (score, source) 的 RRF 合并
        # 使用 chunk_id 作为去重键
        rrf_scores: dict[str, float] = {}
        id_to_source: dict[str, RetrievalSource] = {}

        # 向量结果 → RRF 分数
        if vector_result and vector_result.success:
            for rank, src in enumerate(vector_result.sources, start=1):
                chunk_id = src.chunk_id
                rrf_scores[chunk_id] = vector_weight / (rrf_k + rank)
                id_to_source[chunk_id] = src

        # 关键词结果 → RRF 分数
        for rank, hit in enumerate(keyword_hits, start=1):
            chunk_id = hit.chunk_id
            kw_score = keyword_weight / (rrf_k + rank)
            if chunk_id in rrf_scores:
                rrf_scores[chunk_id] += kw_score
            else:
                rrf_scores[chunk_id] = kw_score
                # 将 KeywordHit 转换为 RetrievalSource
                id_to_source[chunk_id] = RetrievalSource(
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    tenant_id=hit.tenant_id,
                    title=hit.title,
                    section_title=hit.section_title,
                    text=hit.text,
                    score=rrf_scores[chunk_id],  # 会更新
                    source_type=(hit.metadata or {}).get("source_type", ""),
                    metadata=hit.metadata,
                )

        # 按 RRF 分数排序
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        sources = []
        for chunk_id, rrf_score in ranked:
            src = id_to_source.get(chunk_id)
            if src is None:
                continue
            src.score = round(rrf_score, 6)
            sources.append(src)

        # Agent 知识作用域后过滤（已经在向量检索中做了，但 keyword 可能在作用域外）
        if allowed_document_ids is not None:
            sources = [s for s in sources if s.document_id in allowed_document_ids]

        # Build trace_hits: per-channel rank/score + final RRF rank/score
        trace_hits: list[dict] = []
        # Vector channel hits
        if vector_result and vector_result.success:
            for rank, src in enumerate(vector_result.sources, start=1):
                trace_hits.append({
                    "chunk_id": src.chunk_id,
                    "document_id": src.document_id,
                    "channel": "vector",
                    "channel_rank": rank,
                    "channel_score": src.score,
                    "final_rank": _find_rank(src.chunk_id, ranked),
                    "final_score": rrf_scores.get(src.chunk_id),
                    "selected_for_context": src.chunk_id in {cid for cid, _ in ranked},
                })
        # Keyword channel hits
        for rank, hit in enumerate(keyword_hits, start=1):
            trace_hits.append({
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "channel": "keyword",
                "channel_rank": rank,
                "channel_score": hit.score,
                "final_rank": _find_rank(hit.chunk_id, ranked),
                "final_score": rrf_scores.get(hit.chunk_id),
                "selected_for_context": hit.chunk_id in {cid for cid, _ in ranked},
            })

        return RetrievalResult(
            sources=sources,
            query=query,
            success=bool(sources) or (vector_result and vector_result.success),
            degraded=not sources,
            trace_hits=trace_hits,
        )

    async def retrieve_for_task(
        self,
        tenant_id: str,
        message: str,
        task_type: str,
        needs_retrieval: bool = True,
        allowed_document_ids: set[str] | None = None,
        knowledge_version_id: str | None = None,
    ) -> RetrievalResult:
        """根据任务类型条件触发混合检索。"""
        if not needs_retrieval and task_type not in ("knowledge_qa",):
            return RetrievalResult(
                query=message,
                success=True,
                degraded=False,
                skip_reason="task_does_not_need_enterprise_facts",
            )

        start = __import__("time").monotonic()
        result = await self.retrieve(
            tenant_id, message,
            allowed_document_ids=allowed_document_ids,
            knowledge_version_id=knowledge_version_id,
        )
        result.retrieval_latency_ms = (__import__("time").monotonic() - start) * 1000

        if task_type == "knowledge_qa" and not result.has_results:
            result.degraded = True
            result.skip_reason = "knowledge_qa_no_results"

        return result
