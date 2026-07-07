"""Tests for the retrieval router node and ontology step functions."""
from types import SimpleNamespace

import pytest
from langgraph.runtime import Runtime
from langgraph.types import Send
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


def _make_settings(*, engine="legacy_rag", hybrid_retrieval=False,
                   neo4j_uri=None, parallel_enabled=True):
    """Build a minimal mock settings object exposing only the fields read by
    select_retrieval_path."""
    return SimpleNamespace(
        ontology=SimpleNamespace(
            knowledge_engine=engine,
            hybrid_retrieval=hybrid_retrieval,
        ),
        neo4j=SimpleNamespace(uri=neo4j_uri),
        retrieval=SimpleNamespace(parallel_enabled=parallel_enabled),
    )


class TestSelectRetrievalPath:
    """Matrix tests for select_retrieval_path config-driven routing.

    Verifies that ``hybrid`` engine value and ``hybrid_retrieval`` flag —
    previously dead config in the graph path — now trigger parallel
    ontology+rag fan-out, while preserving legacy ontology_neo4j behavior.
    """

    def _state(self, **overrides):
        base = {
            "needs_retrieval": True,
            "knowledge_policy": "required",
            "tenant_id": "t1", "user_id": "u1",
            "message": "test", "conversation_id": "c1", "channel": "local",
        }
        base.update(overrides)
        return base

    def _patch(self, monkeypatch, **kw):
        monkeypatch.setattr(
            "sales_agent.core.config.get_settings",
            lambda: _make_settings(**kw),
        )

    def _assert_fanout(self, result):
        """Assert result is a 2-element Send fan-out (ontology + rag)."""
        assert isinstance(result, list) and len(result) == 2
        assert all(isinstance(s, Send) and s.node == "retrieve" for s in result)
        paths = sorted(s.arg["retrieval_path"] for s in result)
        assert paths == ["ontology", "rag"]

    def test_skip_when_no_retrieval_needed(self, monkeypatch):
        self._patch(monkeypatch, engine="hybrid", neo4j_uri="bolt://x")
        assert select_retrieval_path(self._state(needs_retrieval=False)) == "skip"

    def test_skip_when_knowledge_policy_none(self, monkeypatch):
        self._patch(monkeypatch, engine="hybrid", neo4j_uri="bolt://x")
        assert select_retrieval_path(
            self._state(knowledge_policy="none")) == "skip"

    def test_hybrid_engine_triggers_fanout(self, monkeypatch):
        """taishan 场景：hybrid engine + neo4j → 并行 fan-out（修前是死配置）。"""
        self._patch(monkeypatch, engine="hybrid", neo4j_uri="bolt://neo4j:7687")
        self._assert_fanout(select_retrieval_path(self._state()))

    def test_hybrid_retrieval_flag_triggers_fanout(self, monkeypatch):
        """hybrid_retrieval 标志（legacy 语义）+ neo4j → 并行 fan-out。"""
        self._patch(monkeypatch, engine="legacy_rag",
                    hybrid_retrieval=True, neo4j_uri="bolt://neo4j:7687")
        self._assert_fanout(select_retrieval_path(self._state()))

    def test_hybrid_engine_without_neo4j_degrades_to_rag(self, monkeypatch):
        """hybrid engine 但 neo4j 未配置 → 安全降级到 rag。"""
        self._patch(monkeypatch, engine="hybrid", neo4j_uri=None)
        assert select_retrieval_path(self._state()) == "rag"

    def test_hybrid_retrieval_without_neo4j_degrades_to_rag(self, monkeypatch):
        self._patch(monkeypatch, engine="legacy_rag",
                    hybrid_retrieval=True, neo4j_uri=None)
        assert select_retrieval_path(self._state()) == "rag"

    def test_ontology_neo4j_parallel_enabled_fanout(self, monkeypatch):
        """原行为：ontology_neo4j + parallel_enabled → 并行。"""
        self._patch(monkeypatch, engine="ontology_neo4j",
                    neo4j_uri="bolt://neo4j:7687", parallel_enabled=True)
        self._assert_fanout(select_retrieval_path(self._state()))

    def test_ontology_neo4j_parallel_disabled_solo(self, monkeypatch):
        """原行为：ontology_neo4j + parallel_enabled=False → ontology 独占。"""
        self._patch(monkeypatch, engine="ontology_neo4j",
                    neo4j_uri="bolt://neo4j:7687", parallel_enabled=False)
        assert select_retrieval_path(self._state()) == "ontology"

    def test_legacy_rag_returns_rag(self, monkeypatch):
        """legacy_rag 即使配了 neo4j 也只走 rag。"""
        self._patch(monkeypatch, engine="legacy_rag",
                    neo4j_uri="bolt://neo4j:7687", parallel_enabled=True)
        assert select_retrieval_path(self._state()) == "rag"


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
