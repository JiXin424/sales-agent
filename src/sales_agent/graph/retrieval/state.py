"""State definition for the ontology retrieval subgraph."""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class OntologyRetrievalState(TypedDict, total=False):
    """State flowing through the ontology retrieval subgraph.

    Each key is populated by one node and consumed by the next.
    """

    # === Input ===
    question: str
    tenant_id: str
    agent_id: str | None
    task_type: str

    # === Step 1: Entity extraction ===
    search_terms: list[str]

    # === Step 2: Graph traversal ===
    graph_rows: list[dict[str, Any]]
    vector_fallback_used: bool

    # === Step 3: Evidence compaction ===
    compacted_evidence: dict[str, Any]

    # === Step 4: Answer generation ===
    answer: dict[str, Any]
    sources: list[dict[str, Any]]
    graph_evidence: Any  # GraphEvidence dataclass

    # === Control ===
    error: str | None
