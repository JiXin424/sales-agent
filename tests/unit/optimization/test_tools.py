"""Test constrained optimization tools: tenant enforcement, evidence, single-category."""

import pytest
from sales_agent.optimization.tools import (
    propose_router_patch,
    propose_retrieval_patch,
    propose_document_patch,
    RouterPatchInput,
    RetrievalPatchInput,
    DocumentPatchInput,
)
from sales_agent.optimization.patch_validation import (
    validate_candidate_patch,
    PatchValidationError,
)
from sales_agent.optimization.types import CandidatePatch


class TestConstrainedTools:
    def test_propose_router_patch_uses_provided_tenant(self):
        """tenant_id comes from the tool caller, not from input."""
        result = propose_router_patch(
            tenant_id="tenant-a",
            agent_id="agent-1",
            input_data=RouterPatchInput(rules_json='{"key":"value"}'),
        )
        assert result.tenant_id == "tenant-a"
        assert result.change_type == "router"
        assert result.success is True
        assert result.patch_hash != ""

    def test_propose_retrieval_patch_generates_hash(self):
        result = propose_retrieval_patch(
            tenant_id="t1",
            agent_id="a1",
            input_data=RetrievalPatchInput(synonyms_json='{"视频":["视听"]}'),
        )
        assert result.change_type == "retrieval"
        assert len(result.patch_hash) == 16

    def test_document_patch_without_evidence_becomes_gap(self):
        """Document patches without evidence_ids become knowledge gaps."""
        result = propose_document_patch(
            tenant_id="t1",
            agent_id="a1",
            input_data=DocumentPatchInput(diff="+ invented fact"),
        )
        assert result.action == "create_knowledge_gap"
        assert result.patch_hash == ""

    def test_document_patch_with_evidence_creates_candidate(self):
        result = propose_document_patch(
            tenant_id="t1",
            agent_id="a1",
            input_data=DocumentPatchInput(
                document_id="doc1",
                evidence_ids=["ev1", "ev2"],
                diff="+ corrected discount from 5% to 6%",
            ),
        )
        assert result.action == "create_candidate"
        assert result.patch_hash != ""

    def test_validate_rejects_non_allowlisted_change_type(self):
        with pytest.raises(PatchValidationError):
            validate_candidate_patch(CandidatePatch(
                change_type="prompt", hypothesis="", diagnosis_id="",
            ))

    def test_validate_rejects_router_without_rules(self):
        with pytest.raises(PatchValidationError):
            validate_candidate_patch(CandidatePatch(
                change_type="router", hypothesis="", diagnosis_id="",
            ))

    def test_validate_accepts_valid_router_patch(self):
        patch = CandidatePatch(
            change_type="router",
            hypothesis="test",
            diagnosis_id="diag1",
            router_rules_json='{"key":"value"}',
        )
        result = validate_candidate_patch(patch)
        assert result.change_type == "router"


class TestReleaseGates:
    def test_safety_failure_cannot_be_offset_by_score_gain(self):
        from sales_agent.optimization.gates import ReleaseGates
        gates = ReleaseGates()
        result = gates.evaluate({
            "target_improvement": 0.40,
            "safety_violations": 1,
        })
        assert result.allowed is False
        assert "safety" in result.hard_failures[0].lower()

    def test_cross_tenant_leakage_blocks_release(self):
        from sales_agent.optimization.gates import ReleaseGates
        gates = ReleaseGates()
        result = gates.evaluate({
            "target_improvement": 0.50,
            "cross_tenant_leakage": 1,
        })
        assert result.allowed is False
        assert any("leakage" in f.lower() for f in result.hard_failures)

    def test_clean_candidate_passes_gates(self):
        from sales_agent.optimization.gates import ReleaseGates
        gates = ReleaseGates()
        result = gates.evaluate({
            "target_improvement": 0.25,
            "fixed_regression": 0.01,
            "fact_errors": 0,
            "fabrication_count": 0,
            "safety_violations": 0,
            "cross_tenant_leakage": 0,
            "latency_p95_ms": 5000,
            "token_total": 100000,
            "error_count": 0,
        })
        assert result.allowed is True
        assert len(result.hard_failures) == 0
