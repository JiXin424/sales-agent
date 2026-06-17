"""pgvector-backed vector store for document chunk search."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, delete, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.document import DocumentChunk, Document
from sales_agent.models.base import generate_id

logger = logging.getLogger(__name__)


class VectorStore:
    """Async vector store backed by pgvector."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add_chunks(
        self,
        tenant_id: str,
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Add chunks with embeddings to the vector store.

        Args:
            tenant_id: Tenant identifier for row-level isolation.
            document_id: Parent document identifier.
            chunks: Each dict must contain ``text``, ``chunk_index``,
                ``section_title``, ``metadata`` (dict), and ``embedding``
                (list[float]).

        Returns:
            Number of chunks added.
        """
        if not chunks:
            return 0

        for chunk_data in chunks:
            metadata = chunk_data.get("metadata", {})
            embedding = chunk_data.get("embedding")

            obj = DocumentChunk(
                id=generate_id(),
                tenant_id=tenant_id,
                document_id=document_id,
                chunk_index=chunk_data["chunk_index"],
                text=chunk_data["text"],
                section_title=chunk_data.get("section_title", ""),
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                embedding=embedding,
            )
            self.db.add(obj)

        await self.db.flush()
        logger.info(
            "Added %d chunks for document %s (tenant %s)",
            len(chunks),
            document_id,
            tenant_id,
        )
        return len(chunks)

    async def search(
        self,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.35,
    ) -> list[dict[str, Any]]:
        """Search for similar chunks within a tenant's knowledge base.

        Uses cosine distance via pgvector (``embedding <=> query_vector``).
        Results are filtered by ``tenant_id`` at the SQL level.

        Args:
            tenant_id: Tenant identifier for row-level isolation.
            query_embedding: The embedding vector to search against.
            top_k: Maximum number of results to return.
            min_score: Minimum cosine similarity (``1 - distance``) threshold.

        Returns:
            List of dicts with keys: ``id``, ``tenant_id``, ``document_id``,
            ``text``, ``section_title``, ``metadata``, ``score``, ``title``.
        """
        # Build parameterised query using pgvector cosine distance operator.
        # Score = 1 - cosine_distance = cosine similarity.
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.tenant_id,
                DocumentChunk.document_id,
                DocumentChunk.text,
                DocumentChunk.section_title,
                DocumentChunk.metadata_json,
                Document.title.label("doc_title"),
                (1 - DocumentChunk.embedding.cosine_distance(query_embedding)).label(
                    "score"
                ),
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.tenant_id == tenant_id)
            .where(DocumentChunk.embedding.isnot(None))
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        # Filter by min_score in application layer (pgvector distance is
        # computed server-side but thresholding here avoids SQL complexity).
        results: list[dict[str, Any]] = []
        for row in rows:
            score = float(row.score) if row.score is not None else 0.0
            if score < min_score:
                continue

            metadata: dict[str, Any] = {}
            if row.metadata_json:
                try:
                    metadata = json.loads(row.metadata_json)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            results.append(
                {
                    "id": row.id,
                    "tenant_id": row.tenant_id,
                    "document_id": row.document_id,
                    "text": row.text,
                    "section_title": row.section_title or "",
                    "metadata": metadata,
                    "score": score,
                    "title": row.doc_title or "",
                }
            )

        return results

    async def delete_by_document(self, tenant_id: str, document_id: str) -> int:
        """Delete all chunks belonging to a document.

        Args:
            tenant_id: Tenant identifier for row-level isolation.
            document_id: Document whose chunks should be removed.

        Returns:
            Number of chunks deleted.
        """
        # First count the chunks to be deleted.
        count_stmt = (
            select(DocumentChunk.id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == document_id,
            )
        )
        count_result = await self.db.execute(count_stmt)
        chunk_ids = [row.id for row in count_result.all()]

        if not chunk_ids:
            return 0

        stmt = (
            delete(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == document_id,
            )
        )
        await self.db.execute(stmt)
        await self.db.flush()

        logger.info(
            "Deleted %d chunks for document %s (tenant %s)",
            len(chunk_ids),
            document_id,
            tenant_id,
        )
        return len(chunk_ids)
