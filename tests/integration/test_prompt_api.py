"""Prompt API 集成测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def client():
    """创建测试客户端（同步，不连接真实 DB）。"""
    from sales_agent.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestPromptAPI:
    """测试 Prompt 管理 API 端点。

    注意：这些测试验证 API schema 和路由注册。
    由于 TestClient 是同步的，使用独立的 async 测试验证核心逻辑。
    """

    def test_prompts_router_registered(self, client):
        """prompts 路由应已注册。"""
        # OpenAPI schema 应包含 prompts 路径
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json().get("paths", {})
        # 检查至少一个 prompts 路径存在
        prompt_paths = [p for p in paths if "/prompts" in p]
        assert len(prompt_paths) > 0, "No prompt routes found in OpenAPI schema"

    def test_admin_router_registered(self, client):
        """admin 路由应已注册。"""
        response = client.get("/openapi.json")
        paths = response.json().get("paths", {})
        admin_paths = [p for p in paths if "/admin" in p]
        assert len(admin_paths) > 0, "No admin routes found in OpenAPI schema"

    def test_knowledge_router_registered(self, client):
        """knowledge 路由应已注册。"""
        response = client.get("/openapi.json")
        paths = response.json().get("paths", {})
        knowledge_paths = [p for p in paths if "/knowledge" in p]
        assert len(knowledge_paths) > 0, "No knowledge routes found in OpenAPI schema"

    def test_feedback_list_route_registered(self, client):
        """租户级反馈查询路由应已注册。"""
        response = client.get("/openapi.json")
        paths = response.json().get("paths", {})
        feedback_paths = [p for p in paths if "/feedback" in p]
        assert len(feedback_paths) > 0, "No feedback routes found in OpenAPI schema"


class TestPromptAPISchema:
    """验证 Pydantic schemas。"""

    def test_prompt_version_create_schema(self):
        from sales_agent.api.schemas import PromptVersionCreate
        obj = PromptVersionCreate(
            task_type="knowledge_qa",
            template_text="Test {message}",
        )
        assert obj.task_type == "knowledge_qa"
        assert obj.template_text == "Test {message}"

    def test_prompt_preview_request_schema(self):
        from sales_agent.api.schemas import PromptPreviewRequest
        obj = PromptPreviewRequest(
            task_type="knowledge_qa",
            sample_message="测试消息",
            run_generation=False,
        )
        assert obj.sample_message == "测试消息"
        assert obj.run_generation is False

    def test_ingestion_job_response_schema(self):
        from sales_agent.api.schemas import IngestionJobResponse
        obj = IngestionJobResponse(
            id="job_001",
            tenant_id="t1",
            status="completed",
        )
        assert obj.status == "completed"

    def test_feedback_detail_response_schema(self):
        from sales_agent.api.schemas import FeedbackDetailResponse
        obj = FeedbackDetailResponse(
            id="fb_001",
            tenant_id="t1",
            conversation_id="conv_001",
            user_id="u1",
            rating="up",
        )
        assert obj.rating == "up"
