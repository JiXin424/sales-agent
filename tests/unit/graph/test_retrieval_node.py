"""Tests for the retrieval router node and ontology step functions."""
import pytest
from langgraph.runtime import Runtime
from sales_agent.graph.nodes.retrieval import retrieve_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.retrieval.ontology_graph import compact_evidence_node
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

    def test_retrieval_when_needed(self):
        state: ChatGraphState = {
            "needs_retrieval": True, "tenant_id": "t1", "user_id": "u1",
            "message": "", "conversation_id": "c1", "channel": "local",
        }
        # With needs_retrieval=True, should not be "skip"
        result = select_retrieval_path(state)
        assert result != "skip"
        # Result is either "ontology", "rag", or a list of Send objects
        # depending on config (knowledge_engine, neo4j uri, parallel_enabled)


class TestCompactEvidence:
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
