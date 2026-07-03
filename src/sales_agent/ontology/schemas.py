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

    def to_context_text(self) -> str:
        """将图谱证据格式化为 LLM 可读的上下文字符串。

        供 hybrid 模式使用——把 ontology 检索结果注入 execute_agent 的 prompt。
        """
        lines = ["## 知识图谱检索结果（Ontology Neo4j）", ""]

        if self.matched_entities:
            lines.append(f"**匹配实体** ({len(self.matched_entities)} 个)：")
            for e in self.matched_entities[:5]:
                name = e.get("name") or e.get("title", "?")
                etype = e.get("type", "")
                lines.append(f"  - {name}" + (f" ({etype})" if etype else ""))
            lines.append("")

        if self.facts_used:
            lines.append(f"**图谱事实** ({len(self.facts_used)} 条)：")
            for f in self.facts_used[:10]:
                predicate = f.get("predicate") or f.get("name", "")
                value = f.get("value") or f.get("content") or f.get("text", "")
                if predicate and value:
                    lines.append(f"  - {predicate}: {value}")
                elif value:
                    lines.append(f"  - {value}")
                elif predicate:
                    lines.append(f"  - {predicate}")
            lines.append("")

        if self.evidence:
            lines.append(f"**证据片段** ({len(self.evidence)} 条)：")
            for ev in self.evidence[:5]:
                text = ev.get("content") or ev.get("text") or ev.get("value", "")
                if text:
                    lines.append(f"  > {str(text)[:300]}")
            lines.append("")

        if self.source_documents:
            lines.append(f"**来源文档** ({len(self.source_documents)} 个)：")
            for doc in self.source_documents[:5]:
                title = doc.get("title") or doc.get("name", "?")
                lines.append(f"  - {title}")
            lines.append("")

        return "\n".join(lines) if len(lines) > 2 else ""


@dataclass
class OntologyAnswer:
    answer: dict[str, Any]
    sources: list[dict[str, Any]]
    graph_evidence: GraphEvidence
