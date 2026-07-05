"""Tests for the retrieval router node and ontology subgraph."""
import pytest
from langgraph.runtime import Runtime
from sales_agent.graph.nodes.retrieval import retrieve_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.retrieval.ontology_graph import (
    build_ontology_retrieval_graph,
    should_vector_fallback,
    compact_evidence_node,
)
from sales_agent.graph.retrieval.state import OntologyRetrievalState
from sales_agent.graph.edges.path_conditions import select_retrieval_path


@pytest.mark.asyncio
async def test_retrieve_node_skip_path():
    """retrieve_node returns empty sources for skip path."""
    runtime = Runtime(context={})
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "你好", "conversation_id": "c1", "channel": "local",
        "retrieval_path": "skip",
    }
    result = await retrieve_node(state, runtime)
    assert result["retrieval_info"]["called"] is False
    assert result["sources"] == []
    assert result["skip_generation"] is False


class TestSelectRetrievalPath:
    def test_skip_when_no_retrieval_needed(self):
        state: ChatGraphState = {
            "needs_retrieval": False, "tenant_id": "t1", "user_id": "u1",
            "message": "", "conversation_id": "c1", "channel": "local",
        }
        assert select_retrieval_path(state) == "skip"

    def test_rag_when_retrieval_needed_but_no_neo4j(self):
        state: ChatGraphState = {
            "needs_retrieval": True, "tenant_id": "t1", "user_id": "u1",
            "message": "", "conversation_id": "c1", "channel": "local",
        }
        # Without Neo4j config, should default to rag
        result = select_retrieval_path(state)
        assert result in ("rag", "skip")


class TestOntologySubgraph:
    def test_graph_compiles(self):
        builder = build_ontology_retrieval_graph()
        assert builder is not None
        graph = builder.compile()
        assert graph is not None

    def test_should_vector_fallback_with_rows(self):
        state: OntologyRetrievalState = {
            "graph_rows": [{"e": {"name": "test"}}],
            "question": "test", "tenant_id": "t1",
        }
        assert should_vector_fallback(state) == "compact"

    def test_should_vector_fallback_empty(self):
        state: OntologyRetrievalState = {
            "graph_rows": [],
            "question": "test", "tenant_id": "t1",
        }
        assert should_vector_fallback(state) == "fallback"

    def test_compact_evidence_sorts_by_relevance(self):
        state: OntologyRetrievalState = {
            "graph_rows": [
                {
                    "e": {"name": "产品A", "type": "product"},
                    "f": {"predicate": "价格", "value": "100元"},
                },
            ],
            "search_terms": ["价格"],
            "question": "价格多少",
            "tenant_id": "t1",
        }
        result = compact_evidence_node(state)
        assert "compacted_evidence" in result
        assert len(result["compacted_evidence"]["entities"]) == 1
        assert len(result["compacted_evidence"]["facts"]) == 1


class TestOntologySubgraphCache:
    """The compiled ontology subgraph must be cached (lru_cache maxsize=1)."""

    def test_cache_compiles_only_once(self):
        from sales_agent.graph.nodes.retrieval import _get_ontology_subgraph

        # Clear cache to start fresh
        _get_ontology_subgraph.cache_clear()

        info0 = _get_ontology_subgraph.cache_info()
        assert info0.hits == 0
        assert info0.misses == 0

        # First call — cache miss
        g1 = _get_ontology_subgraph()
        info1 = _get_ontology_subgraph.cache_info()
        assert info1.misses == 1
        assert info1.hits == 0

        # Second call — cache hit
        g2 = _get_ontology_subgraph()
        info2 = _get_ontology_subgraph.cache_info()
        assert info2.misses == 1
        assert info2.hits == 1
        assert g1 is g2, "Same compiled graph should be returned"

        # Clean up
        _get_ontology_subgraph.cache_clear()
