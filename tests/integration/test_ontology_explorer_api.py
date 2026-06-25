"""本体探索器端点（stats / query / query/stream）集成测试。"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.retrieval_service import OntologyRetrievalService


class _FakeClient:
    """占位 Neo4j 客户端：仅需提供 close() 协程。"""

    enabled = True

    async def close(self):
        return None


def _fake_services_factory():
    """返回一个可替换 _build_explorer_services 的 async 工厂。

    使用与 test_ontology_end_to_end_fake.py 同款内存 repository + 假 embedding/chat。
    """
    from tests.integration.test_ontology_end_to_end_fake import (
        MemoryRepository, FakeEmbedding, FakeChat,
    )

    repo = MemoryRepository()
    # 预置一条实体 + 一条事实，供 retrieve 命中
    repo.entities = [{
        "id": "e1", "name": "福利卡", "type": "Product", "tenant_id": "t",
        "canonical_key": "福利卡", "aliases_text": "福利卡",
    }]
    repo.facts = [{
        "fact_id": "f1", "predicate": "description", "value": "员工福利产品",
        "excerpt": "福利卡是员工福利产品。", "source_document_id": "d1", "source_title": "sample.md",
    }]

    async def _factory(agent, db):
        retrieval = OntologyRetrievalService(repo, FakeEmbedding())
        answer = OntologyAnswerService(retrieval, FakeChat())
        return _FakeClient(), retrieval, answer

    return _factory


@pytest.mark.asyncio
async def test_explorer_router_paths_registered():
    from sales_agent.api.routes.ontology import router

    paths = {route.path for route in router.routes}
    assert "/agents/{agent_id}/ontology/stats" in paths
    assert "/agents/{agent_id}/ontology/query" in paths
    assert "/agents/{agent_id}/ontology/query/stream" in paths


@pytest.mark.asyncio
async def test_stats_returns_503_when_neo4j_not_configured(db_session, sample_tenant):
    """测试环境未配置 Neo4j，stats 端点应返回 503。"""
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    from sales_agent.api.routes.ontology import ontology_stats

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    with pytest.raises(Exception) as exc_info:
        await ontology_stats(agent.id, db_session)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_query_returns_503_when_neo4j_not_configured(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    from sales_agent.api.routes.ontology import ontology_query, OntologyQueryRequest

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    with pytest.raises(Exception) as exc_info:
        await ontology_query(agent.id, OntologyQueryRequest(query="福利卡是什么"), db_session)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_query_happy_path_via_fakes(db_session, sample_tenant, monkeypatch):
    """用假服务替换 _build_explorer_services，验证 /query 完整返回结构。"""
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    from sales_agent.main import app
    from sales_agent.api.deps import get_db_session
    from sales_agent.api.routes import ontology as ont_routes

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    monkeypatch.setattr(ont_routes, "_build_explorer_services", _fake_services_factory())

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/agents/{agent.id}/ontology/query",
                json={"query": "福利卡是什么"},
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "福利卡是什么"
    assert "answer" in data and "summary" in data["answer"]
    assert data["search_process"]["query"] == "福利卡是什么"
    assert data["search_process"]["strategy"] in ("graph", "graph_vector_fallback")
    assert "system_prompt" in data["full_context"]
    assert data["full_context"]["user_query"] == "福利卡是什么"


@pytest.mark.asyncio
async def test_query_stream_emits_sse_events(db_session, sample_tenant, monkeypatch):
    """/query/stream 应按序输出 step/search_process/result 四类 SSE 事件。"""
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    from sales_agent.main import app
    from sales_agent.api.deps import get_db_session
    from sales_agent.api.routes import ontology as ont_routes

    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    monkeypatch.setattr(ont_routes, "_build_explorer_services", _fake_services_factory())

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/agents/{agent.id}/ontology/query/stream",
                json={"query": "福利卡是什么"},
            )
            body = resp.content.decode("utf-8")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # 解析所有 SSE data 行
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    types = [e["type"] for e in events]
    assert types.count("step") >= 4  # 4 个 step
    assert "search_process" in types
    assert "result" in types
    # search_process 与 result 内容正确
    sp = next(e for e in events if e["type"] == "search_process")
    assert sp["data"]["query"] == "福利卡是什么"
    res = next(e for e in events if e["type"] == "result")
    assert "answer" in res and "full_context" in res
    assert res["full_context"]["user_query"] == "福利卡是什么"
