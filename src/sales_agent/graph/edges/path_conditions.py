"""Conditional edge functions for the ChatPipeline graph.

Each function receives the current `ChatGraphState` and returns a string
that maps to a destination node (or "fast"/"normal" for the initial split).
These are pure functions — no DB, no LLM, no side effects.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState

_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}


def is_fast_command(state: ChatGraphState) -> str:
    """Determine if the input is a fast command (help/reset).

    Returns:
        "fast" if the message is a help or reset command, "normal" otherwise.
    """
    stripped = state["message"].strip()

    if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
        return "fast"
    if stripped in _RESET_COMMANDS:
        return "fast"
    return "normal"


def select_retrieval_path(state: ChatGraphState) -> str:
    """Select retrieval strategy based on task type and config.

    Returns:
        "ontology" -- Neo4j knowledge graph retrieval (Plan B subgraph)
        "rag" -- Traditional vector/hybrid/keyword retrieval
        "skip" -- No retrieval needed (emotional support, script gen, etc.)
    """
    if not state.get("needs_retrieval"):
        return "skip"

    from sales_agent.core.config import get_settings
    settings = get_settings()

    if settings.ontology.knowledge_engine == "ontology_neo4j" and settings.neo4j.uri:
        return "ontology"

    return "rag"
