"""Ontology retrieval as a LangGraph subgraph.

Decomposes the existing OntologyRetrievalService + OntologyAnswerService
pipeline into observable, individually-configurable graph nodes.

Pipeline:
    extract_terms (LLM) -> graph_query (Cypher) -> [vector_fallback?]
    -> compact_evidence -> generate_answer (LLM) -> END
"""

from __future__ import annotations

import json
import logging
import time
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, TimeoutPolicy

from sales_agent.graph.retrieval.state import OntologyRetrievalState
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)

# -- LLM entity extraction prompt (from OntologyRetrievalService) --
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""

# -- Answer generation prompt (from OntologyAnswerService) --
_ONTOLOGY_RESPONSE_PROMPT = """你是销售知识图谱回答器。基于图谱事实回答用户问题，不要编造。

图谱证据：
{graph_json}

用户问题：{question}
任务类型：{task_type}

输出 JSON：
{{"answer":"自然语言回答","evidence":["使用的事实或来源"],"confidence":0.8}}"""

# -- Evidence compaction limits --
# 从配置读取（运行时参数，优化器可调）
def _max_entities() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_entities_for_prompt

def _max_facts() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_facts_for_prompt


# ============================================================
# Step 1: Extract entity names / keywords from the question
# ============================================================
async def extract_terms_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """LLM extracts entity names/keywords from the user question.

    Uses a tiny LLM call (temperature=0, max_tokens=100) to avoid
    maintaining keyword tables. Falls back to raw question on failure.
    """
    chat_model = runtime.context.get("chat_model")
    question = state["question"]

    if chat_model is None:
        logger.info("Ontology entity extraction (graph): no chat_model, using raw question")
        return {"search_terms": [question]}

    try:
        raw = await chat_model.generate(
            messages=[{
                "role": "user",
                "content": _ENTITY_EXTRACTION_PROMPT.format(question=question),
            }],
            temperature=0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            terms = [str(t).strip() for t in parsed if str(t).strip()]
        elif isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list):
                    terms = [str(t).strip() for t in val if str(t).strip()]
                    break
            else:
                terms = [str(v).strip() for v in parsed.values() if str(v).strip()]
        else:
            terms = [question]
        result_terms = list(dict.fromkeys(terms)) if terms else [question]
        logger.info(
            "Ontology entity extraction (graph): question=%r → terms=%s",
            question[:120], result_terms,
        )
        return {"search_terms": result_terms}
    except Exception:
        logger.warning("Entity extraction failed, falling back to raw question", exc_info=True)
        return {"search_terms": [question]}


# ============================================================
# Step 2: Cypher graph traversal + optional vector fallback
# ============================================================
async def graph_query_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Run Cypher query: match entities by keyword -> traverse to facts/evidence/docs.

    If no entities are matched, the conditional edge routes to vector_fallback.
    """
    settings = get_settings()
    if not settings.neo4j.uri:
        return {"graph_rows": [], "vector_fallback_used": False}

    from sales_agent.ontology.neo4j_client import Neo4jClient
    from sales_agent.ontology.repository import OntologyRepository

    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)

    rows = await repository.retrieve_by_query({
        "tenant_id": state["tenant_id"],
        "agent_id": state.get("agent_id"),
        "search_terms": state.get("search_terms", []),
        "limit": 200,
    })

    # Log entity matches for debugging
    entity_names = []
    for row in rows:
        e = row.get("e")
        if e and isinstance(e, dict):
            entity_names.append(f"{e.get('name', '?')}({e.get('type', '?')})")
    logger.info(
        "Ontology graph query (graph): terms=%s → %d rows, entities=%s",
        state.get("search_terms", []), len(rows),
        entity_names[:10] if entity_names else "NONE",
    )

    return {
        "graph_rows": rows,
        "vector_fallback_used": False,
    }


async def vector_fallback_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Vector embedding fallback when keyword match returns no entities.

    Embeds the question, queries the Neo4j vector index, and retrieves
    the top-5 matching entities with their facts/evidence/documents.
    """
    settings = get_settings()
    embedding_model = runtime.context.get("embedding_model")

    if not settings.neo4j.uri or embedding_model is None:
        return {"graph_rows": [], "vector_fallback_used": True}

    from sales_agent.ontology.neo4j_client import Neo4jClient
    from sales_agent.ontology.repository import OntologyRepository

    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)

    question = state["question"]
    embedding = (await embedding_model.embed([question]))[0]

    rows = await repository.query_vector({
        "tenant_id": state["tenant_id"],
        "agent_id": state.get("agent_id"),
        "embedding": embedding,
        "limit": 5,
    })

    return {
        "graph_rows": rows,
        "vector_fallback_used": True,
    }


# ============================================================
# Step 3: Compact evidence for LLM prompt
# ============================================================
def compact_evidence_node(state: OntologyRetrievalState) -> dict:
    """Compress graph evidence to fit within LLM context window.

    Limits: max 10 entities, max 25 facts (sorted by keyword relevance).
    Each fact keeps only subject/predicate/object/value (value truncated to 200 chars).
    """
    rows = state.get("graph_rows", [])
    search_terms = state.get("search_terms", [])
    question = state.get("question", "")

    # Parse nodes from rows (mirrors OntologyRetrievalService._node)
    def _node(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return dict(value)
        except Exception:
            return {"value": str(value)}

    entities = []
    facts = []
    documents = []
    evidence = []

    for row in rows:
        if row.get("e"):
            entities.append(_node(row["e"]))
        if row.get("f"):
            facts.append(_node(row["f"]))
        for d in (row.get("documents") or []):
            if d:
                documents.append(_node(d))
        for ev in (row.get("evidence") or []):
            if ev:
                evidence.append(_node(ev))

    # Relevance sort: facts matching search terms score higher
    def _fact_score(f: dict) -> int:
        fv = str(f.get("value", "")) + str(f.get("predicate", ""))
        return sum(1 for t in search_terms if t in fv)

    facts.sort(key=_fact_score, reverse=True)

    # Compact
    compacted = {
        "ontology_intent": "entity_info",
        "entities": [
            {"name": e.get("name", ""), "type": e.get("type", "")}
            for e in entities[:_max_entities()]
        ],
        "facts": [
            {
                "subject": f.get("subject_name", f.get("name", "")),
                "predicate": f.get("predicate", ""),
                "object": f.get("object_name", ""),
                "value": str(f.get("value", ""))[:200],
            }
            for f in facts[:_max_facts()]
        ],
        "source_documents": [d.get("title", "") for d in documents[:5]],
        "confidence": 0.8 if entities else 0.0,
    }

    return {"compacted_evidence": compacted}


# ============================================================
# Step 4: Generate answer from evidence
# ============================================================
async def generate_answer_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Call LLM to generate a structured answer from compacted graph evidence.

    Returns answer in sales-agent standard format: {summary, sections, sources}.
    """
    import re as _re
    chat_model = runtime.context.get("chat_model")
    compacted = state.get("compacted_evidence", {})

    if chat_model is None:
        return {
            "answer": {"summary": "知识图谱不可用", "sections": []},
            "sources": [],
        }

    # Render prompt
    prompt = _ONTOLOGY_RESPONSE_PROMPT.format(
        graph_json=json.dumps(compacted, ensure_ascii=False),
        question=state["question"],
        task_type=state.get("task_type", "knowledge_qa"),
    )

    raw = await chat_model.generate(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1600,
    )

    # Parse JSON from LLM output
    try:
        text = raw.strip()
        fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
        if fenced:
            text = fenced.group(1)
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            from json_repair import repair_json
            parsed = repair_json(text, return_objects=True)
        except Exception:
            parsed = {"answer": raw, "evidence": [], "confidence": 0.0}

    answer_text = str(parsed.get("answer", "")).strip()
    evidence_items = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []
    sections = []
    if evidence_items:
        sections.append({
            "title": "依据摘要",
            "content": "\n".join(f"- {item}" for item in evidence_items if item),
        })

    # Build sources from source_documents
    sources = [
        {
            "document_id": "",
            "title": title,
            "display_title": title,
            "score": compacted.get("confidence", 0.8),
            "source_type": "ontology",
        }
        for title in compacted.get("source_documents", [])[:3]
    ]

    return {
        "answer": {"summary": answer_text, "sections": sections},
        "sources": sources,
    }


# ============================================================
# Conditional edge: route from graph_query -> compact or fallback
# ============================================================
def should_vector_fallback(state: OntologyRetrievalState) -> str:
    """If graph_query returned no results, try vector fallback."""
    rows = state.get("graph_rows", [])
    if not rows:
        return "fallback"
    return "compact"


# ============================================================
# Subgraph builder
# ============================================================
def build_ontology_retrieval_graph() -> StateGraph:
    """Build the ontology retrieval subgraph.

    Returns an UNCOMPILED StateGraph -- caller calls .compile() with
    desired checkpointer/options. When used as a node in another graph,
    LangGraph compiles it automatically.

    Graph structure::

        START -> extract_terms (LLM 1)
                    |
                    v
                graph_query (Cypher)
                    |
            +-------+-------+
            v               v
        (has rows)     (no rows)
            |               |
            |               v
            |       vector_fallback (embedding)
            |               |
            +-------+-------+
                    v
            compact_evidence
                    |
                    v
            generate_answer (LLM 2)
                    |
                    v
                   END
    """
    builder = StateGraph(OntologyRetrievalState)

    # -- Nodes --
    builder.add_node("extract_terms", extract_terms_node)
    builder.add_node("graph_query", graph_query_node)
    builder.add_node("vector_fallback", vector_fallback_node)
    builder.add_node("compact_evidence", compact_evidence_node)
    builder.add_node("generate_answer", generate_answer_node)

    # -- Edges --
    builder.add_edge(START, "extract_terms")
    builder.add_edge("extract_terms", "graph_query")
    builder.add_conditional_edges(
        "graph_query", should_vector_fallback,
        {"compact": "compact_evidence", "fallback": "vector_fallback"},
    )
    builder.add_edge("vector_fallback", "compact_evidence")
    builder.add_edge("compact_evidence", "generate_answer")
    builder.add_edge("generate_answer", END)

    return builder
