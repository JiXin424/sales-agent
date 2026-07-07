"""LangGraph-based orchestration for the Sales Agent pipeline.

Public API:
    build_chat_graph()            -- ChatPipeline as a compiled StateGraph
    build_online_graph()          -- Unified online conversation graph
    GRAPH_REGISTRY                -- Registry of buildable graphs (online, guided-flow, chat)

Checkpoints:
    get_checkpointer()            -- AsyncPostgresSaver for production
    get_checkpointer_sync()       -- InMemorySaver for tests
    get_online_checkpointer_sync()-- Process-level InMemorySaver for Online Graph
"""

from sales_agent.graph.chat.graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer, get_checkpointer_sync, get_online_checkpointer_sync
from sales_agent.graph.online.graph import build_online_graph
from sales_agent.graph.registry import GRAPH_REGISTRY

__all__ = [
    "build_chat_graph",
    "build_online_graph",
    "GRAPH_REGISTRY",
    "get_checkpointer",
    "get_checkpointer_sync",
    "get_online_checkpointer_sync",
]
