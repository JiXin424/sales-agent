"""Tests for the /ready endpoint with checkpoint readiness."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from sales_agent.main import app

    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Mock settings to skip Neo4j checks."""
    settings = MagicMock()
    settings.ontology.knowledge_engine = "legacy_rag"
    return settings


@pytest.fixture
def mock_runtime():
    """Mock TenantRuntime with no startup errors."""
    runtime = MagicMock()
    runtime.tenant_id = "test-tenant"
    runtime.deployment_mode = "dedicated"
    runtime.validate_startup.return_value = []
    runtime.get_debug_info.return_value = {}
    return runtime


def test_ready_endpoint_returns_not_ready_when_checkpoint_not_ready(
    client, mock_settings, mock_runtime
):
    """When checkpoint is not ready, /ready returns not_ready with error."""
    with (
        patch("sales_agent.core.config.get_settings", return_value=mock_settings),
        patch(
            "sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime
        ),
        patch(
            "sales_agent.graph.checkpoint_runtime.production_checkpoint_ready",
            return_value=False,
        ),
    ):
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_ready"
        assert "PostgreSQL checkpoint runtime is not ready" in data["errors"]


def test_ready_endpoint_returns_ready_when_checkpoint_ready(
    client, mock_settings, mock_runtime
):
    """When checkpoint is ready, /ready returns ready with checkpoint info."""
    with (
        patch("sales_agent.core.config.get_settings", return_value=mock_settings),
        patch(
            "sales_agent.core.tenant_runtime.get_tenant_runtime", return_value=mock_runtime
        ),
        patch(
            "sales_agent.graph.checkpoint_runtime.production_checkpoint_ready",
            return_value=True,
        ),
    ):
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "checkpoint" in data
        assert data["checkpoint"]["backend"] == "postgresql"
        assert data["checkpoint"]["ready"] is True
