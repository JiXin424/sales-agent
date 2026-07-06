"""验证 retrieve 节点 ontology 空结果时触发 web 兜底。"""

from types import SimpleNamespace

import pytest
from sales_agent.graph.nodes.retrieval import _retrieve_via_ontology, _retrieve_via_rag


def _fake_settings():
    return SimpleNamespace(
        web_search=SimpleNamespace(enabled=True, api_key="sk-test", top_n=5),
        retrieval=SimpleNamespace(top_k=5, mode="hybrid"),
    )


class _FakeRuntime:
    def __init__(self):
        self.context = {"chat_model": None, "db": None, "embedding_model": None}
        self.stream_writer = lambda _evt: None


@pytest.mark.asyncio
async def test_ontology_empty_triggers_web_fallback(monkeypatch):
    """ontology 无 graph_rows + vector_fallback 也空 → 调 web_fallback。"""
    async def fake_extract(local, runtime): return {"search_terms": ["x"]}
    async def fake_query(local, runtime): return {"graph_rows": []}
    async def fake_vec(local, runtime): return {"graph_rows": [], "vector_fallback_used": True}
    def fake_compact(local): return {"compacted_evidence": {"entities": [], "facts": [], "source_documents": [], "confidence": 0.5}}
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.extract_terms_node", fake_extract)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.graph_query_node", fake_query)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.vector_fallback_node", fake_vec)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.compact_evidence_node", fake_compact)

    async def fake_web(*, message, tenant_id, runtime, api_key, top_n):
        return {"ontology_context_text": "## 联网搜索分析\n网搜结论", "sources": [{"title": "T"}], "web_used": True}
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.web_fallback_and_analyze", fake_web)

    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.get_settings", _fake_settings)

    runtime = _FakeRuntime()
    state = {"message": "某问题", "tenant_id": "t1", "task_type": "knowledge_qa"}
    result = await _retrieve_via_ontology(state, runtime, "t1", None, "knowledge_qa", "某问题")

    assert "网搜结论" in result["ontology_context_text"]
    assert result["retrieval_info"]["web_search_used"] is True


@pytest.mark.asyncio
async def test_ontology_nonempty_skips_web_fallback(monkeypatch):
    """ontology 有事实 → 不调 web_fallback，正常返回。"""
    async def fake_extract(local, runtime): return {"search_terms": ["x"]}
    async def fake_query(local, runtime): return {"graph_rows": [{"name": "E1"}]}
    async def fake_vec(local, runtime): return {"graph_rows": [], "vector_fallback_used": False}
    def fake_compact(local): return {"compacted_evidence": {"entities": [{"name": "E1", "type": "Product"}], "facts": [{"subject": "E1", "predicate": "has", "object": "V"}], "source_documents": ["D1"], "confidence": 0.9}}
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.extract_terms_node", fake_extract)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.graph_query_node", fake_query)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.vector_fallback_node", fake_vec)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.compact_evidence_node", fake_compact)

    def fail_web(*a, **kw): raise AssertionError("web fallback should NOT be called when ontology has facts")
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.web_fallback_and_analyze", fail_web)
    monkeypatch.setattr("sales_agent.graph.nodes.retrieval.get_settings", _fake_settings)

    runtime = _FakeRuntime()
    state = {"message": "某问题", "tenant_id": "t1", "task_type": "knowledge_qa"}
    result = await _retrieve_via_ontology(state, runtime, "t1", None, "knowledge_qa", "某问题")

    assert result["retrieval_info"]["web_search_used"] is False
    assert "E1" in result["ontology_context_text"]
