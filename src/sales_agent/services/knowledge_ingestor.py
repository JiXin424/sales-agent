"""Knowledge ingestion pipeline — parse, chunk, embed, store."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.base import EmbeddingModel
from sales_agent.models.base import generate_id
from sales_agent.models.document import Document, DocumentChunk, SourceFile
from sales_agent.rag.chunker import chunk_document, Chunk
from sales_agent.rag.markdown_parser import parse_markdown, MarkdownDocument
from sales_agent.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


class KnowledgeIngestor:
    """Orchestrate the full knowledge ingestion pipeline."""

    def __init__(self, db: AsyncSession, embedding_model: EmbeddingModel) -> None:
        self.db = db
        self.embedding_model = embedding_model
        self.vector_store = VectorStore(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest_directory(
        self,
        tenant_id: str,
        directory: str | Path,
        rebuild_index: bool = False,
        chunk_size: int = 700,
        chunk_overlap: int = 120,
    ) -> dict[str, Any]:
        """Ingest all markdown files from a directory.

        Args:
            tenant_id: Tenant identifier.
            directory: Path to directory containing ``.md`` files.
            rebuild_index: If *True*, delete existing chunks for each document
                before re-ingesting.
            chunk_size: Target chunk size in characters.
            chunk_overlap: Overlap between chunks in characters.

        Returns:
            Dict with keys: ``documents_seen``, ``documents_ingested``,
            ``chunks_created``, ``warnings``, ``errors``.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return {
                "documents_seen": 0,
                "documents_ingested": 0,
                "chunks_created": 0,
                "warnings": [f"Directory does not exist: {directory}"],
                "errors": [],
            }

        md_files = sorted(directory.rglob("*.md"))
        if not md_files:
            return {
                "documents_seen": 0,
                "documents_ingested": 0,
                "chunks_created": 0,
                "warnings": [f"No .md files found in {directory}"],
                "errors": [],
            }

        documents_seen = len(md_files)
        documents_ingested = 0
        total_chunks = 0
        warnings: list[str] = []
        errors: list[dict[str, str]] = []

        for md_file in md_files:
            try:
                result = await self._ingest_file(
                    tenant_id=tenant_id,
                    file_path=md_file,
                    rebuild_index=rebuild_index,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
                if result is not None:
                    documents_ingested += 1
                    total_chunks += result
            except Exception as exc:
                error_msg = f"Failed to ingest {md_file}: {exc}"
                logger.error(error_msg, exc_info=True)
                errors.append({"file": str(md_file), "error": str(exc)})

        return {
            "documents_seen": documents_seen,
            "documents_ingested": documents_ingested,
            "chunks_created": total_chunks,
            "warnings": warnings,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ingest_file(
        self,
        tenant_id: str,
        file_path: Path,
        rebuild_index: bool,
        chunk_size: int,
        chunk_overlap: int,
    ) -> int | None:
        """Ingest a single markdown file.

        Returns the number of chunks created, or *None* if the file was
        skipped.
        """
        # 1. Parse the markdown file.
        try:
            doc = parse_markdown(file_path)
        except ValueError as exc:
            logger.warning("Skipping %s: %s", file_path, exc)
            return None

        # 2. Compute content hash.
        raw_content = file_path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        # 3. Check if document already exists (by source_path + tenant).
        doc_id = await self._upsert_document(
            tenant_id=tenant_id,
            doc=doc,
            content_hash=content_hash,
        )

        # 4. If rebuild_index, delete existing chunks.
        if rebuild_index:
            await self.vector_store.delete_by_document(tenant_id, doc_id)

        # 5. Check if content changed (skip if hash matches and not rebuilding).
        if not rebuild_index:
            existing = await self._find_source_file(tenant_id, content_hash)
            if existing is not None:
                logger.info("Skipping unchanged file: %s", file_path)
                return None

        # 6. Create / update SourceFile record.
        await self._upsert_source_file(
            tenant_id=tenant_id,
            file_path=file_path,
            content_hash=content_hash,
        )

        # 7. Chunk the document.
        chunks = chunk_document(doc, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            logger.warning("No chunks produced for %s", file_path)
            return 0

        # 8. Generate embeddings in batch.
        texts = [c.text for c in chunks]
        try:
            embeddings = await self.embedding_model.embed(texts)
        except Exception as exc:
            logger.error("Embedding failed for %s: %s", file_path, exc)
            raise

        if len(embeddings) != len(chunks):
            raise RuntimeError(
                f"Embedding count mismatch: got {len(embeddings)} "
                f"for {len(chunks)} chunks in {file_path}"
            )

        # 9. Build chunk dicts and store.
        chunk_dicts = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_dicts.append(
                {
                    "text": chunk.text,
                    "chunk_index": chunk.chunk_index,
                    "section_title": chunk.section_title,
                    "metadata": chunk.metadata,
                    "embedding": embedding,
                }
            )

        count = await self.vector_store.add_chunks(
            tenant_id=tenant_id,
            document_id=doc_id,
            chunks=chunk_dicts,
        )

        logger.info(
            "Ingested %s: %d chunks (doc_id=%s)", file_path, count, doc_id
        )
        return count

    async def _upsert_document(
        self,
        tenant_id: str,
        doc: MarkdownDocument,
        content_hash: str,
    ) -> str:
        """Create or update a Document record.  Returns the document ID."""
        stmt = select(Document).where(
            Document.tenant_id == tenant_id,
            Document.source_path == doc.file_path,
        )
        result = await self.db.execute(stmt)
        existing: Document | None = result.scalar_one_or_none()

        metadata = {
            **doc.metadata,
            "source_type": doc.source_type,
            "content_hash": content_hash,
        }

        if existing is not None:
            existing.title = doc.title
            existing.metadata_json = json.dumps(metadata, ensure_ascii=False)
            existing.status = "active"
            await self.db.flush()
            return existing.id

        doc_id = generate_id()
        new_doc = Document(
            id=doc_id,
            tenant_id=tenant_id,
            title=doc.title,
            source_path=doc.file_path,
            status="active",
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        self.db.add(new_doc)
        await self.db.flush()
        return doc_id

    async def _find_source_file(
        self, tenant_id: str, content_hash: str
    ) -> SourceFile | None:
        """Look up an existing SourceFile by content hash."""
        stmt = select(SourceFile).where(
            SourceFile.tenant_id == tenant_id,
            SourceFile.content_hash == content_hash,
            SourceFile.status == "active",
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _upsert_source_file(
        self,
        tenant_id: str,
        file_path: Path,
        content_hash: str,
    ) -> str:
        """Create or update a SourceFile record.  Returns the source file ID."""
        stmt = select(SourceFile).where(
            SourceFile.tenant_id == tenant_id,
            SourceFile.stored_path == str(file_path),
        )
        result = await self.db.execute(stmt)
        existing: SourceFile | None = result.scalar_one_or_none()

        if existing is not None:
            existing.content_hash = content_hash
            existing.original_filename = file_path.name
            existing.status = "active"
            await self.db.flush()
            return existing.id

        sf_id = generate_id()
        sf = SourceFile(
            id=sf_id,
            tenant_id=tenant_id,
            original_filename=file_path.name,
            stored_path=str(file_path),
            content_hash=content_hash,
            mime_type="text/markdown",
            status="active",
        )
        self.db.add(sf)
        await self.db.flush()
        return sf_id
