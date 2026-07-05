"""Tests for the evidence gate node.

The evidence gate sits between retrieval and generation. It enforces the
``knowledge_policy`` field set by the Evidence Router:

- ``required`` + no accepted sources → block generation, return
  evidence-insufficient answer.
- ``required`` + sources present → allow generation (no-op).
- ``optional`` + no sources → allow generation (no-op).
- ``none`` → allow generation (no-op).
"""

from __future__ import annotations

import pytest
from sales_agent.graph.nodes.evidence_gate import evidence_gate
from sales_agent.graph.state import ChatGraphState


def _make_state(
    knowledge_policy: str = "optional",
    sources: list | None = None,
    skip_generation: bool = False,
    answer_dict: dict | None = None,
) -> ChatGraphState:
    return {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "测试消息",
        "conversation_id": "c1",
        "channel": "local",
        "knowledge_policy": knowledge_policy,
        "sources": sources or [],
        "skip_generation": skip_generation,
        "answer_dict": answer_dict or {},
    }


class TestEvidenceGateRequired:
    """Tests for ``knowledge_policy == "required"``."""

    def test_required_without_sources_blocks_generation(self):
        """When policy is required and sources is empty, generation is blocked."""
        state = _make_state(knowledge_policy="required", sources=[])
        result = evidence_gate(state)

        assert result["skip_generation"] is True
        answer = result["answer_dict"]
        assert "当前知识库中没有找到足够依据" in answer["summary"]
        assert answer["sections"] == []
        assert result["path_reason"] == "required_evidence_missing"

    def test_required_with_sources_continues(self):
        """When policy is required and sources exist, no blocking update."""
        state = _make_state(
            knowledge_policy="required",
            sources=[{"id": "s1", "content": "some evidence"}],
        )
        result = evidence_gate(state)
        assert result == {}

    def test_required_without_sources_but_skipped(self):
        """When skip_generation is already True (e.g. ontology), pass through."""
        state = _make_state(
            knowledge_policy="required",
            sources=[],
            skip_generation=True,
            answer_dict={"summary": "already answered", "sections": []},
        )
        result = evidence_gate(state)
        assert result == {}


class TestEvidenceGateOptional:
    """Tests for ``knowledge_policy == "optional"``."""

    def test_optional_without_sources_continues(self):
        """When policy is optional, missing sources does not block."""
        state = _make_state(knowledge_policy="optional", sources=[])
        result = evidence_gate(state)
        assert result == {}

    def test_optional_with_sources_continues(self):
        """When policy is optional and sources exist, block is not triggered."""
        state = _make_state(
            knowledge_policy="optional",
            sources=[{"id": "s2", "content": "nice to have"}],
        )
        result = evidence_gate(state)
        assert result == {}


class TestEvidenceGateNone:
    """Tests for ``knowledge_policy == "none"``."""

    def test_none_continues(self):
        """When policy is none, gate always allows generation."""
        state = _make_state(knowledge_policy="none", sources=[])
        result = evidence_gate(state)
        assert result == {}

    def test_none_with_sources_continues(self):
        """When policy is none with stray sources, still continues."""
        state = _make_state(
            knowledge_policy="none",
            sources=[{"id": "s3", "content": "stray"}],
        )
        result = evidence_gate(state)
        assert result == {}


class TestEvidenceGateMissingPolicy:
    """When ``knowledge_policy`` is absent from state, assume safe default."""

    def test_missing_policy_continues(self):
        """When no knowledge_policy, gate allows generation (backward compat)."""
        state: ChatGraphState = {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
        }
        result = evidence_gate(state)
        assert result == {}


class TestSelectRetrievalPath:
    """Tests for ``select_retrieval_path`` integration with knowledge_policy."""

    def _make_state(self, needs_retrieval: bool = True, knowledge_policy: str = "required") -> ChatGraphState:
        return {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "test",
            "conversation_id": "c1",
            "channel": "local",
            "needs_retrieval": needs_retrieval,
            "knowledge_policy": knowledge_policy,
        }

    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Disable ontology and parallel settings so select_retrieval_path
        returns simple strings instead of complex Send tuples."""
        monkeypatch.setattr(
            "sales_agent.core.config.get_settings",
            lambda: type(
                "Settings",
                (),
                {
                    "ontology": type("Ont", (), {"knowledge_engine": "none"})(),
                    "neo4j": type("N4j", (), {"uri": None})(),
                    "retrieval": type("Ret", (), {"parallel_enabled": False})(),
                },
            )(),
        )

    def test_knowledge_policy_none_skips_retrieval(self):
        """When knowledge_policy is 'none', return 'skip' even if needs_retrieval."""
        from sales_agent.graph.edges.path_conditions import select_retrieval_path

        state = self._make_state(needs_retrieval=True, knowledge_policy="none")
        result = select_retrieval_path(state)
        assert result == "skip"

    def test_knowledge_policy_required_allows_retrieval(self):
        """When knowledge_policy is 'required', proceed to retrieval."""
        from sales_agent.graph.edges.path_conditions import select_retrieval_path

        state = self._make_state(needs_retrieval=True, knowledge_policy="required")
        result = select_retrieval_path(state)
        assert result in ("ontology", "rag")

    def test_knowledge_policy_optional_allows_retrieval(self):
        """When knowledge_policy is 'optional', proceed to retrieval."""
        from sales_agent.graph.edges.path_conditions import select_retrieval_path

        state = self._make_state(needs_retrieval=True, knowledge_policy="optional")
        result = select_retrieval_path(state)
        assert result in ("ontology", "rag")

    def test_no_needs_retrieval_skips(self):
        """When needs_retrieval is False, skip regardless of knowledge_policy."""
        from sales_agent.graph.edges.path_conditions import select_retrieval_path

        state = self._make_state(needs_retrieval=False, knowledge_policy="required")
        result = select_retrieval_path(state)
        assert result == "skip"
