"""Test versioned knowledge and runtime models are registered and structured correctly."""

import pytest
from sales_agent.core.database import Base


def test_version_tables_are_registered():
    """All eight foundation tables must appear in Base.metadata."""
    expected = {
        "document_revisions",
        "knowledge_versions",
        "knowledge_version_documents",
        "retrieval_profiles",
        "router_profiles",
        "optimization_releases",
        "agent_runtime_bindings",
        "release_events",
    }
    actual = set(Base.metadata.tables)
    missing = expected - actual
    assert not missing, f"Missing tables: {missing}"


def test_document_chunks_are_scoped_to_a_version():
    """DocumentChunk must gain nullable version/revision columns for backward compat."""
    columns = Base.metadata.tables["document_chunks"].c
    assert columns["knowledge_version_id"].nullable is True
    assert columns["document_revision_id"].nullable is True


def test_agent_runtime_binding_has_optimistic_lock():
    """AgentRuntimeBinding must include lock_version for optimistic concurrency."""
    columns = Base.metadata.tables["agent_runtime_bindings"].c
    assert "lock_version" in columns
    assert columns["lock_version"].nullable is False


def test_knowledge_version_document_is_a_join():
    """KnowledgeVersionDocument maps a version to one document revision."""
    columns = Base.metadata.tables["knowledge_version_documents"].c
    assert "knowledge_version_id" in columns
    assert "document_revision_id" in columns
    assert "tenant_id" in columns
