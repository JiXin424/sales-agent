"""Graph Registry — single source of truth for buildable graphs.

Export builders for:
  - ``online``           — Unified online conversation graph (HTTP + DingTalk)
  - ``guided-flow``      — Guided flow state machine (visits / coaching)
  - ``ontology-retrieval`` — Neo4j ontology retrieval subgraph

Legacy graphs (daily-eval, quick-session) are NOT registered here.
"""

from __future__ import annotations

from sales_agent.graph.online_graph import build_online_graph
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
from sales_agent.graph.retrieval.ontology_graph import build_ontology_retrieval_graph

GRAPH_REGISTRY: dict[str, dict] = {
    "online": {
        "name": "Online Conversation",
        "builder": build_online_graph,
    },
    "guided-flow": {
        "name": "Guided Flow",
        "builder": build_guided_flow_graph,
    },
    "ontology-retrieval": {
        "name": "Ontology Retrieval",
        "builder": build_ontology_retrieval_graph,
    },
}
