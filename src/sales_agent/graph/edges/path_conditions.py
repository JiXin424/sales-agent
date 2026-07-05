"""Conditional edge functions for the ChatPipeline graph.

Each function receives the current ``ChatGraphState`` and returns a string
(or ``list[Send]`` for fan-out) that maps to destination node(s).

Send (parallel fan-out): When a node needs to dispatch to multiple
parallel retrievals, return ``list[Send]`` — LangGraph executes them
concurrently and collects the results into state.
"""

from __future__ import annotations

from langgraph.types import Send

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


def select_retrieval_path(state: ChatGraphState):
    """Select retrieval strategy. Supports Send fan-out for parallel retrieval.

    When the config enables ``retrieval.parallel_enabled`` and the task
    requires retrieval, this returns a list of ``Send`` objects that run
    multiple retrieval backends concurrently.

    When ``knowledge_policy`` is ``"none"`` (set by the Evidence Router),
    retrieval is skipped regardless of ``needs_retrieval``.

    Returns:
        "ontology" — Neo4j knowledge graph retrieval (solo)
        "rag" — Traditional vector/hybrid/keyword retrieval (solo)
        "skip" — No retrieval needed

        list[Send] — Parallel fan-out when ``parallel_enabled`` is True.
                     Each Send carries a ``retrieval_backend`` hint so the
                     retrieve_node can distinguish the source.
    """
    if not state.get("needs_retrieval"):
        return "skip"

    # Evidence Router's "none" policy means retrieval should be skipped
    # even when the task type would normally need retrieval.
    if state.get("knowledge_policy") == "none":
        return "skip"

    from sales_agent.core.config import get_settings
    settings = get_settings()

    use_ontology = (
        settings.ontology.knowledge_engine == "ontology_neo4j"
        and settings.neo4j.uri
    )

    # ── Send fan-out: parallel retrieval across backends ──────────
    if getattr(settings.retrieval, "parallel_enabled", False) and use_ontology:
        # Fan out to ontology + vector retrieval concurrently.
        # Each Send must carry the full node input so the node can
        # execute with complete context. Results merge via `sources: add`.
        ctx = {
            "tenant_id": state["tenant_id"],
            "message": state["message"],
            "task_type": state.get("task_type", "knowledge_qa"),
            "agent_id": state.get("agent_id"),
        }
        return [
            Send("retrieve", {**ctx, "retrieval_path": "ontology"}),
            Send("retrieve", {**ctx, "retrieval_path": "rag"}),
        ]

    if use_ontology:
        return "ontology"
    return "rag"
