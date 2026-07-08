"""LangGraph-based orchestration for the Sales Agent pipeline.

Public API:
    build_chat_graph()            -- ChatPipeline as a compiled StateGraph
    build_online_graph()          -- Unified online conversation graph
    GRAPH_REGISTRY                -- Registry of buildable graphs (online, guided-flow, chat)

Checkpoints:
    get_checkpointer()                       -- Strict AsyncPostgresSaver for production
    get_checkpointer_sync()                  -- InMemorySaver for tests
    initialize_production_checkpointer()     -- Initialize PostgreSQL runtime
    get_production_checkpointer()            -- Get production checkpointer
    close_production_checkpointer()          -- Shutdown production runtime
    production_checkpoint_ready()            -- Check if production runtime is ready
    CheckpointUnavailableError               -- Runtime not ready exception
"""

from sales_agent.graph.chat.graph import build_chat_graph
from sales_agent.graph.checkpoints import (
    CheckpointUnavailableError,
    get_checkpointer,
    get_checkpointer_sync,
    initialize_production_checkpointer,
    get_production_checkpointer,
    close_production_checkpointer,
    production_checkpoint_ready,
)
from sales_agent.graph.online.graph import build_online_graph
from sales_agent.graph.registry import GRAPH_REGISTRY

__all__ = [
    "build_chat_graph",
    "build_online_graph",
    "GRAPH_REGISTRY",
    "CheckpointUnavailableError",
    "get_checkpointer",
    "get_checkpointer_sync",
    "initialize_production_checkpointer",
    "get_production_checkpointer",
    "close_production_checkpointer",
    "production_checkpoint_ready",
]
