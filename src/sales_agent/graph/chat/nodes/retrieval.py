"""Retrieval router node for the ChatPipeline graph.

Routes to one of three paths based on `select_retrieval_path`:
  - "ontology": Run ontology retrieval steps directly (extract → query →
               compact) and return evidence text for the main generate node.
  - "rag": Traditional vector/hybrid/keyword retrieval via existing services
  - "skip": Bypass retrieval entirely (e.g. emotional support, script gen)

All paths return ``skip_generation=False`` — the main ``generate_node``
always produces the final answer with PromptRegistry-resolved prompts.

P1: Emits custom stream events via ``runtime.stream_writer`` for
     progress tracking.
"""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from sales_agent.graph.chat.state import ChatGraphState
from sales_agent.core.config import get_settings
from sales_agent.graph.retrieval.ontology_graph import (
    extract_terms_node,
    graph_query_node,
    vector_fallback_node,
    compact_evidence_node,
)
from sales_agent.graph.retrieval.web_fallback import web_fallback_and_analyze
from sales_agent.graph.retrieval.gap_fill import compute_missing

logger = logging.getLogger(__name__)


async def retrieve_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Execute retrieval based on the path selected by select_retrieval_path.

    Called with state["retrieval_path"] already set by the conditional edge.

    P1: Uses ``runtime.stream_writer`` for custom progress events.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context (db, chat_model, embedding_model).

    Returns:
        Dict with retrieval results. Sets ``ontology_context_text`` for
        the main generate node when the ontology path is used; never sets
        ``skip_generation=True``.
    """
    writer = runtime.stream_writer
    path = state.get("retrieval_path", "skip")
    task_type = state.get("task_type", "knowledge_qa")
    message = state["message"]
    tenant_id = state["tenant_id"]
    agent_id = state.get("agent_id")

    # P1: Custom stream progress
    writer({"phase": "retrieval_started", "path": path, "task_type": task_type})

    # -- Path 0: Web search (out-of-domain / unknown, policy "web") --
    # Primary Bocha web search — NOT a KB-empty fallback. Used when the
    # Evidence Router decides the query is clearly outside the sales domain
    # (sports results, news, general knowledge, tech/code, etc.).
    if path == "web":
        return await _retrieve_via_web(message=message, tenant_id=tenant_id, runtime=runtime)

    # -- Path 1: Ontology Neo4j knowledge graph (inline steps, no subgraph) --
    if path == "ontology":
        return await _retrieve_via_ontology(
            state, runtime, tenant_id, agent_id, task_type, message,
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


async def _retrieve_via_web(
    *,
    message: str,
    tenant_id: str,
    runtime: Runtime,
) -> dict:
    """Primary web-search retrieval for out-of-domain queries (policy "web").

    Calls Bocha web search + LLM analysis (``web_fallback_and_analyze``) as
    the PRIMARY retrieval path — not as a KB-empty fallback. Returns web
    sources (``source_type="web"``) so the citation block labels them
    「网络搜索」. On failure / no key / no results, returns empty sources;
    ``evidence_gate`` then lets ``generate`` produce a clean answer without
    fake KB citations (policy is "web", not "required", so the gate does not
    block on empty).
    """
    writer = runtime.stream_writer
    settings = get_settings()
    writer({"phase": "web_search"})
    web_result = await web_fallback_and_analyze(
        message=message,
        tenant_id=tenant_id,
        runtime=runtime,
        api_key=settings.web_search.api_key,
        top_n=settings.web_search.top_n,
    )
    if web_result is not None:
        return {
            "retrieval_info": {
                "called": True,
                "provider": "web_search",
                "source_count": len(web_result["sources"]),
                "web_search_used": True,
            },
            "sources": web_result["sources"],
            "skip_generation": False,
            "ontology_context_text": web_result["ontology_context_text"],
        }
    writer({"phase": "web_search_empty"})
    return {
        "retrieval_info": {
            "called": True,
            "provider": "web_search",
            "source_count": 0,
            "web_search_used": True,
        },
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
) -> dict:
    """Run ontology retrieval steps directly and return evidence text.

    Pipeline: extract_terms (LLM) → graph_query (Cypher) →
    [vector_fallback] → compact_evidence → evidence text.

    Does NOT generate a final answer — the evidence text is passed to the
    main ``generate_node`` which applies PromptRegistry-resolved prompts
    for formatting, sales tone, and markdown output.
    """
    writer = runtime.stream_writer

    # Local state dict that drives the step functions
    local: dict = {
        "question": message,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "task_type": task_type,
        "search_terms": [],
        "graph_rows": [],
        "vector_fallback_used": False,
        "compacted_evidence": {},
    }

    try:
        # Step 1: Extract search terms (LLM)
        writer({"phase": "ontology_extracting_terms"})
        local.update(await extract_terms_node(local, runtime))
        logger.info(
            "Ontology retrieval: terms=%s",
            local.get("search_terms", [])[:5],
        )

        # Step 2: Graph query (Cypher)
        writer({"phase": "ontology_querying_graph"})
        local.update(await graph_query_node(local, runtime))

        # Step 3: Vector fallback if no graph results
        if not local.get("graph_rows"):
            writer({"phase": "ontology_vector_fallback"})
            local.update(await vector_fallback_node(local, runtime))

        # Step 4: Compact evidence
        writer({"phase": "ontology_compacting"})
        local.update(compact_evidence_node(local))

    except Exception as e:
        logger.warning("Ontology retrieval failed: %s", e, exc_info=True)
        return {
            "retrieval_info": {
                "called": True,
                "provider": "ontology_neo4j",
                "error": str(e),
            },
            "sources": [],
            "skip_generation": False,
            "ontology_context_text": "",
        }

    # Step 5: Build KB context block from compacted evidence
    compacted = local.get("compacted_evidence", {})
    entities = compacted.get("entities", [])
    facts = compacted.get("facts", [])
    docs = compacted.get("source_documents", [])

    kb_lines: list[str] = []
    if entities or facts or docs:
        kb_lines.append("## 知识图谱（本体）检索结果")
        if entities:
            kb_lines.append(f"匹配实体 ({len(entities)}): " + ", ".join(
                f"{e.get('name', '')}({e.get('type', '')})" for e in entities[:20]
            ))
        if facts:
            kb_lines.append(f"相关事实 ({len(facts)}):")
            for f in facts[:15]:
                kb_lines.append(
                    f"  - [{f.get('subject', '')}] {f.get('predicate', '')} "
                    f"{f.get('object', '')} {f.get('value', '')}"[:200]
                )
        if docs:
            kb_lines.append(f"来源文档: {', '.join(docs[:10])}")
    kb_text = "\n".join(kb_lines)

    # Step 6: 缺口补全--对 KB 未命中的实体走定向 web 搜索。
    # 用 search_terms（期望实体）减去命中实体名，得到未命中实体；
    # 每个 web 结果的 context 块与 sources 追加合并到 KB 结果（非替换）。
    settings = get_settings()
    matched_names = [e.get("name", "") for e in entities]
    missing = compute_missing(
        local.get("search_terms", []),
        matched_names,
        max_n=settings.web_search.max_gap_entities,
    )

    web_text_parts: list[str] = []
    web_sources: list[dict] = []
    if missing and settings.web_search.enabled:
        writer({"phase": "web_gap_fill", "missing_entities": missing})
        for entity in missing:
            try:
                web_result = await web_fallback_and_analyze(
                    message=message,
                    search_query=f"{entity} 产品 功能 介绍",
                    context_message=message,
                    tenant_id=tenant_id,
                    runtime=runtime,
                    api_key=settings.web_search.api_key,
                    top_n=settings.web_search.top_n,
                )
            except Exception as e:
                logger.warning("Web gap-fill failed for entity=%r: %s", entity, e)
                web_result = None
            if web_result is not None:
                web_text_parts.append(web_result["ontology_context_text"])
                web_sources.extend(web_result["sources"])

    web_used = bool(web_text_parts)

    # 合并 context 文本：KB 块在前，web 块（## 联网搜索分析）在后。
    parts = [p for p in [kb_text, *web_text_parts] if p.strip()]
    ontology_context_text = "\n".join(parts)

    # Build KB sources from source_documents.
    # text 字段携带完整检索上下文（KB+web 合并），供 eval retrieval_context 使用；
    # 钉钉 renderer 仍取 title/display_title 做文末引用。
    sources = [
        {
            "document_id": "",
            "title": title,
            "display_title": title,
            "text": ontology_context_text,
            "score": compacted.get("confidence", 0.8),
            "source_type": "ontology",
        }
        for title in docs[:3]
    ]

    writer({
        "phase": "ontology_retrieval_complete",
        "entity_count": len(entities),
        "fact_count": len(facts),
        "web_search_used": web_used,
    })

    return {
        "retrieval_info": {
            "called": True,
            "provider": "ontology_neo4j",
            "vector_fallback_used": local.get("vector_fallback_used", False),
            "source_count": len(sources) + len(web_sources),
            "web_search_used": web_used,
        },
        "sources": sources + web_sources,
        "skip_generation": False,
        "ontology_context_text": ontology_context_text,
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

    # Web 兜底——RAG 无结果时
    if not sources and settings.web_search.enabled:
        writer({"phase": "web_fallback"})
        web_result = await web_fallback_and_analyze(
            message=message,
            tenant_id=tenant_id,
            runtime=runtime,
            api_key=settings.web_search.api_key,
            top_n=settings.web_search.top_n,
        )
        if web_result is not None:
            return {
                "retrieval_info": {
                    "called": True,
                    "top_k": settings.retrieval.top_k,
                    "source_count": len(web_result["sources"]),
                    "web_search_used": True,
                },
                "sources": web_result["sources"],
                "retrieval_result": None,
                "skip_generation": False,
                "ontology_context_text": web_result["ontology_context_text"],
            }

    return {
        "retrieval_info": {
            "called": True,
            "top_k": settings.retrieval.top_k,
            "source_count": len(sources),
            "web_search_used": False,
        },
        "sources": sources,
        "retrieval_result": retrieval_result,
        "skip_generation": False,
    }
