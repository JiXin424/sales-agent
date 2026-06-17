"""RAG 检索服务：基于 pgvector 的相似度检索，强制 tenant_id 过滤。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

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
    ) -> RetrievalResult:
        """执行检索。

        Args:
            tenant_id: 租户 ID（强制过滤）
            query: 检索文本
            top_k: 返回数量，默认从配置读取
            min_score: 最低相似度，默认从配置读取
            allowed_document_ids: Agent 知识作用域允许的 document_id 集合。
                None 表示不限（tenant 全量）；空集合表示 Agent 作用域为空。

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
                tenant_id, message, allowed_document_ids=allowed_document_ids
            )
            result.retrieval_latency_ms = (__import__("time").monotonic() - start) * 1000
            if not result.has_results:
                result.degraded = True
                result.skip_reason = "knowledge_qa_no_results"
            return result

        # 其他任务类型的条件检索
        start = __import__("time").monotonic()
        result = await self.retrieve(
            tenant_id, message, allowed_document_ids=allowed_document_ids
        )
        result.retrieval_latency_ms = (__import__("time").monotonic() - start) * 1000
        return result
