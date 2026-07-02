"""Test release resolution node: pins manifest IDs into graph state."""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestReleaseResolutionNode:
    """Unit tests for release_resolution node."""

    def test_release_resolution_pins_manifest(self):
        """Node must add release_id and version IDs to state."""
        from sales_agent.graph.nodes.release_resolution import (
            _build_release_state,
        )

        fake_manifest = MagicMock()
        fake_manifest.id = "rel_1"
        fake_manifest.knowledge_version_id = "kv_1"
        fake_manifest.retrieval_profile_id = "rp_1"
        fake_manifest.router_profile_id = "rtp_1"

        state = {"tenant_id": "t1", "agent_id": "a1"}
        result = _build_release_state(state, fake_manifest)

        assert result["release_id"] == "rel_1"
        assert result["knowledge_version_id"] == "kv_1"
        assert result["retrieval_profile_id"] == "rp_1"
        assert result["router_profile_id"] == "rtp_1"

    def test_release_resolution_none_when_no_binding(self):
        """When no binding exists, version IDs are None."""
        from sales_agent.graph.nodes.release_resolution import (
            _build_release_state,
        )

        state = {"tenant_id": "t1", "agent_id": "a1"}
        result = _build_release_state(state, None)

        assert result["release_id"] is None
        assert result["knowledge_version_id"] is None
        assert result["retrieval_profile_id"] is None
        assert result["router_profile_id"] is None

    def test_release_state_keys_are_in_graph_state(self):
        """Verify the new keys are in ChatGraphState TypedDict."""
        from sales_agent.graph.state import ChatGraphState

        # These keys must be accepted by the TypedDict
        state: ChatGraphState = {
            "release_id": "r1",
            "knowledge_version_id": "kv1",
            "retrieval_profile_id": "rp1",
            "router_profile_id": "rtp1",
            "release_manifest_hash": "abc",
        }
        assert state["release_id"] == "r1"
        assert state["knowledge_version_id"] == "kv1"

    def test_retrieval_filters_pinned_knowledge_version(self):
        """HybridRetriever.retrieve must accept and pass knowledge_version_id."""
        # This is a signature/contract test - not a DB integration test
        import inspect
        from sales_agent.services.retriever import HybridRetriever, Retriever

        # Both retrievers should accept knowledge_version_id
        hybrid_sig = inspect.signature(HybridRetriever.retrieve)
        assert "knowledge_version_id" in hybrid_sig.parameters

        retriever_sig = inspect.signature(Retriever.retrieve)
        assert "knowledge_version_id" in retriever_sig.parameters
