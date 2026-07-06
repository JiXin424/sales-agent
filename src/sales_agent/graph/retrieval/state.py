"""State shape for the ontology retrieval steps.

The steps are now called directly by ``retrieve_node`` using plain dicts
that follow this shape — there is no longer a separate subgraph.
"""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class OntologyRetrievalState(TypedDict, total=False):
    """State shape used by the ontology retrieval step functions.

    This is a documentation contract — the steps accept plain ``dict``
    and read/write keys matching these names.
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

    # === Control ===
    error: str | None
