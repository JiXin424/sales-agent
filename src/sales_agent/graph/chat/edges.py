"""Conditional edge functions for the ChatPipeline graph.

Each function receives the current ``ChatGraphState`` and returns a string
(or ``list[Send]`` for fan-out) that maps to destination node(s).

Send (parallel fan-out): When a node needs to dispatch to multiple
parallel retrievals, return ``list[Send]`` — LangGraph executes them
concurrently and collects the results into state.
"""

from __future__ import annotations

from langgraph.types import Send

from sales_agent.graph.chat.state import ChatGraphState

_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}


# ====================================================================
# Path conditions  (was edges/path_conditions.py)
# ====================================================================


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

    Fan-out (ontology + RAG concurrently) is triggered when the task needs
    retrieval and any of these hold (and Neo4j is configured):

    - ``ontology.hybrid_retrieval`` is True (legacy-aligned hybrid switch)
    - ``ontology.knowledge_engine == "hybrid"`` (mixed engine semantics)
    - ``retrieval.parallel_enabled`` is True with an ontology-capable engine

    When ``knowledge_policy`` is ``"none"`` (set by the Evidence Router),
    retrieval is skipped regardless of ``needs_retrieval``.

    Returns:
        "ontology" — Neo4j knowledge graph retrieval (solo)
        "rag" — Traditional vector/hybrid/keyword retrieval (solo)
        "skip" — No retrieval needed

        list[Send] — Parallel fan-out (ontology + rag). Each Send carries a
                     ``retrieval_path`` hint so retrieve_node can route.
    """
    if not state.get("needs_retrieval"):
        return "skip"

    # Evidence Router's "none" policy means retrieval should be skipped
    # even when the task type would normally need retrieval.
    if state.get("knowledge_policy") == "none":
        return "skip"

    from sales_agent.core.config import get_settings
    settings = get_settings()

    engine = settings.ontology.knowledge_engine
    neo4j_ready = bool(settings.neo4j.uri)
    # 对齐 legacy chat_pipeline 语义：hybrid_retrieval 标志或 ontology_neo4j/hybrid
    # 引擎声明使用本体。config 注释明确支持 hybrid（= 同时跑 ontology + RAG），
    # 故 hybrid engine 值与 hybrid_retrieval 标志同样视为启用本体检索。
    use_hybrid = settings.ontology.hybrid_retrieval
    use_ontology = neo4j_ready and (use_hybrid or engine in ("ontology_neo4j", "hybrid"))

    # ── Send fan-out: parallel retrieval across backends ──────────
    # 并行 fan-out 触发条件（任一）：
    #   - hybrid_retrieval 标志：显式开启混合检索（与 legacy 一致）
    #   - hybrid 引擎：语义即 ontology + RAG 混合
    #   - parallel_enabled：保留 ontology_neo4j 默认并行行为
    parallel = use_ontology and (
        use_hybrid
        or engine == "hybrid"
        or getattr(settings.retrieval, "parallel_enabled", False)
    )
    if parallel:
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


# ====================================================================
# Risk conditions  (was edges/risk_conditions.py)
# ====================================================================


def check_risk_result(state: ChatGraphState) -> str:
    """Route based on risk check outcome.

    Returns:
        "pass" — answer is safe, proceed to log
        "block" — answer blocked, regenerate with safety notice
        "rewrite" — answer needs rewrite, regenerate with rewrite hint
        "human_review" — P0: HITL interrupt was triggered, proceed to log
        "max_retries" — no more retries, proceed anyway
    """
    action = state.get("risk_action", "allow")
    retry_count = state.get("retry_count", 0)

    if action in ("pass", "allow", "warn"):
        return "pass"

    # P0: HITL — human review already handled inside risk_check_node
    if action == "human_review":
        return "human_review"

    if action == "block" and retry_count < 3:
        return "block"
    if action == "rewrite" and retry_count < 3:
        return "rewrite"
    return "max_retries"
