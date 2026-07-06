"""Graph Registry — single source of truth for buildable graphs.

Export builders for:
  - ``online``        — Unified online conversation graph (HTTP + DingTalk)
  - ``guided-flow``   — Guided flow state machine (visits / coaching)
  - ``chat``          — ChatPipeline subgraph (retrieval → generation → risk → log)

Ontology retrieval steps are now called inline by ``retrieve_node``;
there is no longer a separate subgraph to register.

Legacy graphs (daily-eval, quick-session) are NOT registered here.
"""

from __future__ import annotations

from sales_agent.graph.online_graph import build_online_graph
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
from sales_agent.graph.chat_graph import build_chat_graph

GRAPH_REGISTRY: dict[str, dict] = {
    "online": {
        "name": "Online Conversation",
        "builder": build_online_graph,
    },
    "guided-flow": {
        "name": "Guided Flow",
        "builder": build_guided_flow_graph,
    },
    "chat": {
        "name": "Chat",
        "builder": build_chat_graph,
    },
}
