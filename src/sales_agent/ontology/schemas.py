from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceItem:
    excerpt: str
    locator: str = ""
    confidence: float = 0.8
    extraction_method: str = "llm"
    source_document_id: str | None = None


@dataclass
class EntityCandidate:
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class FactCandidate:
    subject_name: str
    predicate: str
    object_name: str | None = None
    value: str | None = None
    fact_type: str = "relation"
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    status: str = "active"
    risk_level: str = "low"
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class OntologyIngestionStats:
    documents_seen: int = 0
    documents_ingested: int = 0
    entities_created: int = 0
    entities_merged: int = 0
    facts_created: int = 0
    facts_active: int = 0
    facts_pending_review: int = 0
    facts_rejected: int = 0
    conflicts_created: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "documents_seen": self.documents_seen,
            "documents_ingested": self.documents_ingested,
            "entities_created": self.entities_created,
            "entities_merged": self.entities_merged,
            "facts_created": self.facts_created,
            "facts_active": self.facts_active,
            "facts_pending_review": self.facts_pending_review,
            "facts_rejected": self.facts_rejected,
            "conflicts_created": self.conflicts_created,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class GraphEvidence:
    ontology_intent: str
    center_entities: list[dict[str, Any]] = field(default_factory=list)
    matched_entities: list[dict[str, Any]] = field(default_factory=list)
    facts_used: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_documents: list[dict[str, Any]] = field(default_factory=list)
    retrieval_strategy: str = "graph"
    vector_fallback_used: bool = False
    confidence: float = 0.0
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology_intent": self.ontology_intent,
            "center_entities": self.center_entities,
            "matched_entities": self.matched_entities,
            "facts_used": self.facts_used,
            "evidence": self.evidence,
            "source_documents": self.source_documents,
            "retrieval_strategy": self.retrieval_strategy,
            "vector_fallback_used": self.vector_fallback_used,
            "confidence": self.confidence,
            "timings_ms": self.timings_ms,
        }


@dataclass
class OntologyAnswer:
    answer: dict[str, Any]
    sources: list[dict[str, Any]]
    graph_evidence: GraphEvidence
