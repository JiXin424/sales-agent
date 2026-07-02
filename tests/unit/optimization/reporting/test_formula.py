"""Test effect formula: weights, normalization, gates, classification, hashes."""

import pytest
from sales_agent.optimization.reporting.formula import (
    EFFECT_V1,
    classify_case,
    compute_effect,
    compute_snapshot_hash,
    get_formula,
)
from sales_agent.optimization.reporting.types import (
    MetricDefinition,
    EffectFormula,
)


class TestFormulaRegistry:
    def test_effect_v1_weights_sum_to_one(self):
        assert sum(EFFECT_V1.group_weights.values()) == pytest.approx(1.0)

    def test_effect_v1_has_all_five_groups(self):
        expected = {"answer_quality", "retrieval", "routing", "safety_refusal", "efficiency"}
        assert set(EFFECT_V1.group_weights) == expected

    def test_get_formula_returns_v1(self):
        f = get_formula("effect-v1")
        assert f.version == "effect-v1"
        assert f is EFFECT_V1

    def test_get_formula_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown formula version"):
            get_formula("effect-v99")


class TestMetricNormalization:
    def test_higher_is_better_normalization(self):
        metric = MetricDefinition("score", "group", direction="higher", minimum=0.0, maximum=1.0)
        assert metric.normalize(0.8) > metric.normalize(0.2)
        assert metric.normalize(1.0) == pytest.approx(1.0)
        assert metric.normalize(0.0) == pytest.approx(0.0)

    def test_lower_is_better_is_normalized_before_delta(self):
        metric = MetricDefinition(
            "p95_latency_ms", "efficiency", direction="lower", minimum=0, maximum=5000,
        )
        # Lower raw → higher normalized score
        assert metric.normalize(1000) > metric.normalize(2000)
        assert metric.normalize(0) == pytest.approx(1.0)
        assert metric.normalize(5000) == pytest.approx(0.0)

    def test_min_equals_max_returns_half(self):
        metric = MetricDefinition("flat", "group", minimum=5.0, maximum=5.0)
        assert metric.normalize(5.0) == 0.5

    def test_clamping(self):
        metric = MetricDefinition("x", "group", minimum=0, maximum=100)
        assert metric.normalize(-10) == 0.0
        assert metric.normalize(200) == 1.0


class TestEffectComputation:
    def test_composite_with_perfect_improvement(self):
        """10% improvement across all groups."""
        baseline = {
            "answer_correctness": 0.7, "factual_accuracy": 0.7, "completeness": 0.7,
            "contextual_precision": 0.7, "contextual_recall": 0.7, "contextual_relevancy": 0.7,
            "route_accuracy": 0.7,
            "safety_pass_rate": 0.7, "refusal_correctness": 0.7, "fabrication_on_unanswerable": 0.3,
            "p95_latency_ms": 4000, "token_usage": 50000,
        }
        candidate = {
            "answer_correctness": 0.8, "factual_accuracy": 0.8, "completeness": 0.8,
            "contextual_precision": 0.8, "contextual_recall": 0.8, "contextual_relevancy": 0.8,
            "route_accuracy": 0.8,
            "safety_pass_rate": 0.8, "refusal_correctness": 0.8, "fabrication_on_unanswerable": 0.2,
            "p95_latency_ms": 3000, "token_usage": 40000,
        }
        decision = compute_effect(EFFECT_V1, "candidate", baseline, candidate)

        assert decision.composite_after > decision.composite_before
        assert decision.composite_delta > 0  # type: ignore[operator]
        assert decision.recommendation == "improved"
        assert len(decision.groups) == 5

    def test_hard_gate_overrides_positive_composite(self):
        """Tenant leakage must produce do_not_publish even with improved scores."""
        baseline = {"answer_correctness": 0.5}
        candidate = {"answer_correctness": 0.8}
        gates = {"tenant_leakage": False}
        decision = compute_effect(EFFECT_V1, "candidate", baseline, candidate, hard_gates=gates)

        assert decision.recommendation == "do_not_publish"
        assert "tenant_leakage" in decision.hard_gates_failed

    def test_critical_fact_error_gate(self):
        """factual_accuracy hard gate failure blocks publish."""
        baseline = {"factual_accuracy": 0.5}
        candidate = {"factual_accuracy": 0.6}
        gates = {"critical_fact_error": False}
        decision = compute_effect(EFFECT_V1, "candidate", baseline, candidate, hard_gates=gates)

        assert decision.recommendation == "do_not_publish"

    def test_final_report_with_hard_gate_rollback(self):
        """Final report with failed gate recommends rollback."""
        baseline = {"safety_pass_rate": 0.5}
        candidate = {"safety_pass_rate": 0.5}
        gates = {"unsafe_response": False}
        decision = compute_effect(EFFECT_V1, "final", baseline, candidate, hard_gates=gates)

        assert decision.recommendation == "rollback_recommended"

    def test_missing_metrics_reduce_coverage(self):
        """Only answer_quality present → composite uses only that group."""
        baseline = {"answer_correctness": 0.7, "factual_accuracy": 0.7, "completeness": 0.7}
        candidate = {"answer_correctness": 0.7, "factual_accuracy": 0.7, "completeness": 0.7}
        decision = compute_effect(EFFECT_V1, "candidate", baseline, candidate)

        # answer_quality group should have coverage=3
        aq = next(g for g in decision.groups if g.group_name == "answer_quality")
        assert aq.coverage == 3
        # Other groups should have coverage=0
        for g in decision.groups:
            if g.group_name != "answer_quality":
                assert g.coverage == 0


class TestCaseClassification:
    def test_error_classification(self):
        effect = classify_case(case_id="c1", before_pass=None, after_pass=None, had_error=True)
        assert effect.classification == "error"

    def test_new_case(self):
        effect = classify_case(case_id="c2", before_pass=None, after_pass=True, is_new_case=True)
        assert effect.classification == "new"

    def test_pass_transition_improved(self):
        effect = classify_case(case_id="c3", before_pass=False, after_pass=True)
        assert effect.classification == "improved"
        assert effect.cause == "pass_transition"

    def test_pass_transition_regressed(self):
        effect = classify_case(case_id="c4", before_pass=True, after_pass=False)
        assert effect.classification == "regressed"

    def test_score_improvement(self):
        effect = classify_case(
            case_id="c5", before_pass=True, after_pass=True,
            before_score=0.7, after_score=0.8,
        )
        assert effect.classification == "improved"
        assert effect.score_delta == pytest.approx(0.1)

    def test_unchanged_within_tolerance(self):
        effect = classify_case(
            case_id="c6", before_pass=True, after_pass=True,
            before_score=0.70, after_score=0.71,
        )
        assert effect.classification == "unchanged"

    def test_rank_and_latency_deltas_preserved(self):
        effect = classify_case(
            case_id="c7", before_pass=True, after_pass=True,
            before_score=0.7, after_score=0.7,
            rank_delta=-3, latency_delta_ms=-150.0, token_delta=-200,
        )
        assert effect.rank_delta == -3
        assert effect.latency_delta_ms == -150.0
        assert effect.token_delta == -200


class TestSnapshotHash:
    def test_deterministic_hash(self):
        a = compute_snapshot_hash({"b": 2, "a": 1})
        b = compute_snapshot_hash({"a": 1, "b": 2})
        assert a == b  # key order independent

    def test_different_inputs_yield_different_hashes(self):
        a = compute_snapshot_hash({"x": 1})
        b = compute_snapshot_hash({"x": 2})
        assert a != b
