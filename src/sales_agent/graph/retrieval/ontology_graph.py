"""Ontology retrieval step functions for the ChatPipeline.

These are called directly by ``retrieve_node`` — there is no longer a
separate subgraph.  The main ``generate_node`` handles answer generation
with full PromptRegistry support.

Pipeline:
    extract_terms (LLM) -> graph_query (Cypher) -> [vector_fallback?]
    -> compact_evidence -> (back to retrieve_node for evidence-text build)
"""

from __future__ import annotations

import json
import logging
from langgraph.runtime import Runtime

from sales_agent.core.config import get_settings
from sales_agent.llm.call_params import get_call_params

logger = logging.getLogger(__name__)

# -- LLM entity extraction prompt (from OntologyRetrievalService) --
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""


# -- Evidence compaction limits --
def _max_entities() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_entities_for_prompt


def _max_facts() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_facts_for_prompt


# ============================================================
# Step 1: Extract entity names / keywords from the question
# ============================================================
async def extract_terms_node(state: dict, runtime: Runtime) -> dict:
    """LLM extracts entity names/keywords from the user question.

    Uses a tiny LLM call (temperature=0, max_tokens=100) to avoid
    maintaining keyword tables. Falls back to raw question on failure.
    """
    chat_model = runtime.context.get("chat_model")
    question = state["question"]

    if chat_model is None:
        logger.info("Ontology entity extraction: no chat_model, using raw question")
        return {"search_terms": [question]}

    # 解析 ontology_term_extractor prompt：runtime.context.get("db") 由生产入口
    # （dingtalk/graph_stream.py:122 / online_conversation.py:228-233）注入；
    # tenant_id/agent_id 直接从 state 取。db/ids 缺失时回退到模块常量。
    from sales_agent.services.prompt_resolver_helper import resolve_knowledge_prompt
    db = runtime.context.get("db")
    tenant_id = state.get("tenant_id")
    agent_id = state.get("agent_id")
    prompt = await resolve_knowledge_prompt(
        db,
        "ontology_term_extractor",
        tenant_id,
        agent_id,
        default=_ENTITY_EXTRACTION_PROMPT,
        question=question,
    )

    try:
        p = get_call_params("ontology_graph")
        raw = await chat_model.generate(
            messages=[{
                "role": "user",
                "content": prompt,
            }],
            temperature=p.temperature,
            max_tokens=p.max_tokens,
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
            "Ontology entity extraction: question=%r → terms=%s",
            question[:120], result_terms,
        )
        return {"search_terms": result_terms}
    except Exception:
        logger.warning("Entity extraction failed, falling back to raw question", exc_info=True)
        return {"search_terms": [question]}


# ============================================================
# Step 2: Cypher graph traversal + optional vector fallback
# ============================================================
async def graph_query_node(state: dict, runtime: Runtime) -> dict:
    """Run Cypher query: match entities by keyword -> traverse to facts/evidence/docs.

    If no entities are matched, the caller should route to vector_fallback.
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
        "Ontology graph query: terms=%s → %d rows, entities=%s",
        state.get("search_terms", []), len(rows),
        entity_names[:10] if entity_names else "NONE",
    )

    return {
        "graph_rows": rows,
        "vector_fallback_used": False,
    }


async def vector_fallback_node(state: dict, runtime: Runtime) -> dict:
    """Vector embedding fallback when keyword match returns no entities.

    Embeds the question, queries the Neo4j vector index, and retrieves
    the top-k matching entities with their facts/evidence/documents.
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
        "limit": settings.ontology.vector_fallback_top_k,
    })

    return {
        "graph_rows": rows,
        "vector_fallback_used": True,
    }


# ============================================================
# Step 3: Compact evidence for LLM prompt
# ============================================================
def compact_evidence_node(state: dict) -> dict:
    """Compress graph evidence to fit within LLM context window.

    Limits: configurable max entities and facts (sorted by keyword relevance).
    Each fact keeps only subject/predicate/object/value (value truncated to 200 chars).
    """
    rows = state.get("graph_rows", [])
    search_terms = state.get("search_terms", [])

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
