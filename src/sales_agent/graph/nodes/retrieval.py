"""Retrieval router node for the ChatPipeline graph.

Routes to one of three paths based on `select_retrieval_path`:
  - "ontology": Delegate to the ontology retrieval subgraph (Plan B)
  - "rag": Traditional vector/hybrid/keyword retrieval via existing services
  - "skip": Bypass retrieval entirely (e.g. emotional support, script gen)

The ontology path runs its own LLM calls internally and sets
`skip_generation=True` -- the main `generate` node is bypassed.

P1: Emits custom stream events via ``runtime.stream_writer`` for
     progress tracking.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from langgraph.runtime import Runtime

from sales_agent.graph.state import ChatGraphState
from sales_agent.core.config import get_settings
from sales_agent.graph.retrieval.ontology_graph import build_ontology_retrieval_graph

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_ontology_subgraph():
    """Return the compiled ontology retrieval subgraph (cached).

    The subgraph builder is stateless (same topology every call), so we
    compile once and reuse across all requests.  The cache can be cleared
    for testing via ``_get_ontology_subgraph.cache_clear()``.
    """
    return build_ontology_retrieval_graph().compile()


async def retrieve_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Execute retrieval based on the path selected by select_retrieval_path.

    Called with state["retrieval_path"] already set by the conditional edge.

    P1: Uses ``runtime.stream_writer`` for custom progress events.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context (db, chat_model, embedding_model).

    Returns:
        Dict with retrieval results. May set `skip_generation=True` and
        `answer_dict` if the ontology subgraph pre-computed the answer.
    """
    writer = runtime.stream_writer
    path = state.get("retrieval_path", "skip")
    task_type = state.get("task_type", "knowledge_qa")
    message = state["message"]
    tenant_id = state["tenant_id"]
    agent_id = state.get("agent_id")
    parallel_mode = state.get("parallel_mode", False)

    # P1: Custom stream progress
    writer({"phase": "retrieval_started", "path": path, "task_type": task_type})

    # -- Path 1: Ontology Neo4j knowledge graph (subgraph) --
    if path == "ontology":
        return await _retrieve_via_ontology(
            state, runtime, tenant_id, agent_id, task_type, message,
            parallel_mode=parallel_mode,
        )

    # -- Path 2: Traditional RAG (vector/hybrid/keyword) --
    if path == "rag":
        return await _retrieve_via_rag(state, runtime, tenant_id, task_type, message)

    # -- Path 3: Skip --
    writer({"phase": "retrieval_skipped", "reason": "path_does_not_need_retrieval"})
    return {
        "retrieval_info": {"called": False, "reason": "path_does_not_need_retrieval"},
        "sources": [],
        "skip_generation": False,
    }


async def _retrieve_via_ontology(
    state: ChatGraphState,
    runtime: Runtime,
    tenant_id: str,
    agent_id: str | None,
    task_type: str,
    message: str,
    parallel_mode: bool = False,
) -> dict:
    """Run the ontology retrieval subgraph, which handles everything internally:
    extract terms -> graph query -> [vector fallback] -> compact -> generate answer.
    """
    subgraph = _get_ontology_subgraph()

    sub_input = {
        "question": message,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "task_type": task_type,
    }

    try:
        sub_result = await subgraph.ainvoke(
            sub_input,
            context=runtime.context,
        )
    except Exception as e:
        logger.warning("Ontology subgraph failed: %s, falling back", e)
        return {
            "retrieval_info": {"called": True, "provider": "ontology_neo4j", "error": str(e)},
            "sources": [],
            "skip_generation": False,
            "answer_dict": None,
        }

    # 并行模式：只返回 sources，让主 generate 节点统一生成（合并 ontology + RAG）
    # 独占模式（非并行）：skip_generation，用 ontology 子图自己生成的 answer
    if parallel_mode:
        # 构建 ontology 证据文本，供主生成节点与 RAG 结果合并
        compacted = sub_result.get("compacted_evidence", {})
        onto_lines = ["## 知识图谱（本体）检索结果"]
        entities = compacted.get("entities", [])
        if entities:
            onto_lines.append(f"匹配实体 ({len(entities)}): " + ", ".join(
                f"{e.get('name','')}({e.get('type','')})" for e in entities[:20]
            ))
        facts = compacted.get("facts", [])
        if facts:
            onto_lines.append(f"相关事实 ({len(facts)}):")
            for f in facts[:15]:
                onto_lines.append(
                    f"  - [{f.get('subject','')}] {f.get('predicate','')} "
                    f"{f.get('object','')} {f.get('value','')}"[:200]
                )
        docs = compacted.get("source_documents", [])
        if docs:
            onto_lines.append(f"来源文档: {', '.join(docs[:10])}")
        ontology_context_text = "\n".join(onto_lines)

        return {
            "retrieval_info": {
                "called": True,
                "provider": "ontology_neo4j",
                "vector_fallback_used": sub_result.get("vector_fallback_used", False),
                "source_count": len(sub_result.get("sources", [])),
                "parallel_mode": True,
            },
            "sources": sub_result.get("sources", []),
            "skip_generation": False,
            "answer_dict": None,
            "ontology_context_text": ontology_context_text,
        }
    return {
        "retrieval_info": {
            "called": True,
            "provider": "ontology_neo4j",
            "vector_fallback_used": sub_result.get("vector_fallback_used", False),
            "source_count": len(sub_result.get("sources", [])),
        },
        "sources": sub_result.get("sources", []),
        "answer_dict": sub_result.get("answer"),       # <- pre-computed answer!
        "skip_generation": True,                       # <- skip main generate node
    }


async def _retrieve_via_rag(
    state: ChatGraphState,
    runtime: Runtime,
    tenant_id: str,
    task_type: str,
    message: str,
) -> dict:
    """Traditional vector/hybrid/keyword retrieval via existing Retriever services."""
    db = runtime.context.get("db")
    embedding_model = runtime.context.get("embedding_model")
    settings = get_settings()
    mode = settings.retrieval.mode
    writer = runtime.stream_writer  # P1

    if db is None:
        writer({"phase": "retrieval_failed", "reason": "no_db"})
        return {"sources": [], "retrieval_result": None, "skip_generation": False}

    from sales_agent.services.retriever import Retriever, HybridRetriever
    from sales_agent.rag.keyword_retriever import KeywordRetriever

    if mode == "keyword":
        kr = KeywordRetriever(db)
        retriever = HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )
    elif mode == "hybrid":
        kr = KeywordRetriever(db)
        retriever = HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )
    else:
        retriever = Retriever(db, embedding_model)

    retrieval_result = await retriever.retrieve_for_task(
        tenant_id=tenant_id,
        message=message,
        task_type=task_type,
        needs_retrieval=True,
    )

    sources = [s.to_source_item() for s in (retrieval_result.sources if retrieval_result else [])]

    # P1: Custom stream — retrieval complete
    writer({
        "phase": "retrieval_complete",
        "mode": mode,
        "source_count": len(sources),
    })

    return {
        "retrieval_info": {
            "called": True,
            "top_k": settings.retrieval.top_k,
            "source_count": len(sources),
        },
        "sources": sources,
        "retrieval_result": retrieval_result,
        "skip_generation": False,
    }
