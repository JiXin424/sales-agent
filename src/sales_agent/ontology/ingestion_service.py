from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.llm.base import EmbeddingModel
from sales_agent.models.base import generate_id, utcnow
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.canonicalizer import canonical_key, normalize_aliases
from sales_agent.ontology.conflict import classify_fact_risk, merge_status_for_risk
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate, OntologyIngestionStats


def _read_content(path: Path) -> str:
    """Read file content. Binary office formats (.docx/.pdf/.pptx) are parsed into
    text by lightweight libs (python-docx / pymupdf / python-pptx). Other files
    (.md/.txt) are read as UTF-8.

    Note: docling was the original choice but its torch-heavy dependency tree
    (2.8GB+) would not install in the target environment (resolver thrash). These
    lightweight libs cover text extraction for the LLM pipeline at a fraction of
    the footprint."""
    ext = path.suffix.lower()
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            raise RuntimeError(f"docx 解析失败：{path.name}")
    if ext == ".pdf":
        try:
            import fitz  # pymupdf
            parts: list[str] = []
            with fitz.open(str(path)) as doc:
                for page in doc:
                    parts.append(page.get_text())
            return "\n".join(parts)
        except Exception:
            raise RuntimeError(f"pdf 解析失败：{path.name}")
    if ext == ".pptx":
        try:
            from pptx import Presentation
            prs = Presentation(str(path))
            texts: list[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text_frame.text)
            return "\n".join(texts)
        except Exception:
            raise RuntimeError(f"pptx 解析失败：{path.name}")
    return path.read_text(encoding="utf-8")


class ExtractorProtocol(Protocol):
    async def extract_entities(self, content: str) -> list[EntityCandidate]: ...
    async def extract_facts(self, content: str, entities: list[EntityCandidate]) -> list[FactCandidate]: ...


class RepositoryProtocol(Protocol):
    async def upsert_entity(self, params: dict) -> tuple[str, bool]: ...
    async def create_fact(self, params: dict) -> str: ...


class OntologyIngestionService:
    def __init__(
        self,
        db: AsyncSession,
        repository: RepositoryProtocol,
        embedding_model: EmbeddingModel,
        extractor: ExtractorProtocol,
    ) -> None:
        self.db = db
        self.repository = repository
        self.embedding_model = embedding_model
        self.extractor = extractor

    async def ingest_paths(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        paths: list[Path],
        progress_callback=None,
    ) -> tuple[IngestionJob, OntologyIngestionStats]:
        job = IngestionJob(
            tenant_id=tenant_id,
            agent_id=agent_id,
            engine="ontology_neo4j",
            status="running",
            stage="uploaded",
            documents_seen=len(paths),
        )
        self.db.add(job)
        await self.db.flush()

        stats = OntologyIngestionStats(documents_seen=len(paths))
        for path in paths:
            try:
                await self._ingest_one(job, stats, tenant_id, agent_id, path,
                                       progress_callback=progress_callback)
                stats.documents_ingested += 1
            except Exception as exc:  # noqa: BLE001
                stats.errors.append({"file": str(path), "error": str(exc)})

        job.documents_ingested = stats.documents_ingested
        job.entities_created = stats.entities_created
        job.entities_merged = stats.entities_merged
        job.facts_created = stats.facts_created
        job.facts_active = stats.facts_active
        job.facts_pending_review = stats.facts_pending_review
        job.facts_rejected = stats.facts_rejected
        job.conflicts_created = stats.conflicts_created
        job.warnings_json = json.dumps(stats.warnings, ensure_ascii=False)
        job.errors_json = json.dumps(stats.errors, ensure_ascii=False)
        job.metadata_json = json.dumps(stats.to_metadata(), ensure_ascii=False)
        job.status = "completed" if not stats.errors else "completed_with_errors"
        job.stage = "completed" if not stats.errors else "completed_with_warnings"
        await self.db.flush()
        return job, stats

    async def _ingest_one(
        self,
        job: IngestionJob,
        stats: OntologyIngestionStats,
        tenant_id: str,
        agent_id: str | None,
        path: Path,
        progress_callback=None,
    ) -> None:
        job.stage = "parsed"
        if progress_callback:
            await progress_callback(job.stage, {
                "entities_created": stats.entities_created,
                "entities_merged": stats.entities_merged,
                "facts_created": stats.facts_created,
                "facts_active": stats.facts_active,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
        content = _read_content(path)
        source_document_id = generate_id()
        now = utcnow()

        job.stage = "extracting_entities"
        if progress_callback:
            await progress_callback(job.stage, {
                "entities_created": stats.entities_created,
                "entities_merged": stats.entities_merged,
                "facts_created": stats.facts_created,
                "facts_active": stats.facts_active,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
        entities = await self.extractor.extract_entities(content)
        embeddings = await self.embedding_model.embed([
            f"{e.name} {json.dumps(e.properties, ensure_ascii=False)}" for e in entities
        ]) if entities else []

        name_to_entity: dict[str, EntityCandidate] = {}
        for entity, embedding in zip(entities, embeddings):
            aliases = normalize_aliases(entity.aliases)
            entity_key = canonical_key(entity.name)
            entity_id, created = await self.repository.upsert_entity({
                "id": generate_id(),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "type": entity.type,
                "name": entity.name,
                "canonical_key": entity_key,
                "aliases": aliases,
                "aliases_text": " ".join(aliases),
                "properties": json.dumps(entity.properties, ensure_ascii=False),
                "embedding": embedding,
                "status": "active",
                "now": now,
            })
            name_to_entity[entity.name] = entity
            if created:
                stats.entities_created += 1
            else:
                stats.entities_merged += 1

        job.stage = "extracting_facts"
        if progress_callback:
            await progress_callback(job.stage, {
                "entities_created": stats.entities_created,
                "entities_merged": stats.entities_merged,
                "facts_created": stats.facts_created,
                "facts_active": stats.facts_active,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
        facts = await self.extractor.extract_facts(content, entities)
        job.stage = "writing_neo4j"
        if progress_callback:
            await progress_callback(job.stage, {
                "entities_created": stats.entities_created,
                "entities_merged": stats.entities_merged,
                "facts_created": stats.facts_created,
                "facts_active": stats.facts_active,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
        for fact in facts:
            subject = name_to_entity.get(fact.subject_name)
            if subject is None:
                stats.warnings.append(f"Fact skipped; subject not found: {fact.subject_name}")
                continue
            object_entity = name_to_entity.get(fact.object_name or "")
            risk = classify_fact_risk(fact)
            status = merge_status_for_risk(risk)
            if status == "active":
                stats.facts_active += 1
            else:
                stats.facts_pending_review += 1
                if risk == "high":
                    stats.conflicts_created += 1
            evidence = fact.evidence[0] if fact.evidence else None
            await self.repository.create_fact({
                "fact_id": generate_id(),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "subject_type": subject.type,
                "subject_key": canonical_key(subject.name),
                "object_type": object_entity.type if object_entity else "",
                "object_key": canonical_key(object_entity.name) if object_entity else "",
                "predicate": fact.predicate,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "properties": json.dumps(fact.properties, ensure_ascii=False),
                "confidence": fact.confidence,
                "status": status,
                "risk_level": risk,
                "version": 1,
                "version_date": None,
                "source_document_id": source_document_id,
                "source_title": path.name,
                "source_file_id": None,
                "source_path": str(path),
                "content_hash": "",
                "evidence_id": generate_id(),
                "excerpt": evidence.excerpt if evidence else content[:300],
                "locator": evidence.locator if evidence else path.name,
                "evidence_confidence": evidence.confidence if evidence else fact.confidence,
                "extraction_method": evidence.extraction_method if evidence else "llm",
                "now": now,
            })
            stats.facts_created += 1
