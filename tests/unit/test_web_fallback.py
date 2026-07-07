"""验证 web_fallback_and_analyze：调 bocha + LLM 分析，返回 analysis 文本。"""

import pytest
from sales_agent.graph.retrieval.web_fallback import web_fallback_and_analyze
from sales_agent.services.web_search import WebSearchResult


class _FakeChatModel:
    def __init__(self, response: str):
        self._response = response

    async def generate(self, messages, **kwargs):
        return self._response


class _FakeRuntime:
    """最小 runtime：含 chat_model + stream_writer。"""
    def __init__(self, chat_model):
        self.context = {"chat_model": chat_model}
        self.stream_writer = lambda _evt: None


@pytest.mark.asyncio
async def test_web_fallback_returns_analysis_when_sources(monkeypatch):
    """bocha 有结果 + LLM 分析成功 → 返回 analysis 拼好的 context。"""
    async def fake_bocha(query, api_key, top_n=5):
        return WebSearchResult(
            query=query, success=True, raw_answer="摘要",
            sources=[{"title": "T1", "url": "http://x", "text": "内容1", "source_type": "web_search"}],
        )
    monkeypatch.setattr("sales_agent.graph.retrieval.web_fallback.bocha_search", fake_bocha)

    llm_resp = '{"analysis":"网搜结论","has_relevant":true,"constraints":"","confidence":"medium"}'
    runtime = _FakeRuntime(_FakeChatModel(llm_resp))

    result = await web_fallback_and_analyze(
        message="某问题", tenant_id="t1", runtime=runtime,
        api_key="sk-test", top_n=5,
    )
    assert result is not None
    assert "网搜结论" in result["ontology_context_text"]
    assert result["web_used"] is True
    assert len(result["sources"]) == 1


@pytest.mark.asyncio
async def test_web_fallback_returns_none_when_disabled():
    """api_key 为空 → 返回 None。"""
    runtime = _FakeRuntime(_FakeChatModel(""))
    result = await web_fallback_and_analyze(
        message="x", tenant_id="t1", runtime=runtime, api_key="", top_n=5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_web_fallback_returns_none_when_no_sources(monkeypatch):
    """bocha 无结果 → 返回 None。"""
    async def fake_bocha(query, api_key, top_n=5):
        return WebSearchResult(query=query, success=False, error="Timeout")
    monkeypatch.setattr("sales_agent.graph.retrieval.web_fallback.bocha_search", fake_bocha)
    runtime = _FakeRuntime(_FakeChatModel(""))
    result = await web_fallback_and_analyze(
        message="x", tenant_id="t1", runtime=runtime, api_key="sk-test", top_n=5,
    )
    assert result is None
