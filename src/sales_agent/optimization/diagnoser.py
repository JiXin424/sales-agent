"""Deterministic failure diagnoser: ordered attribution rules.

Proceeds through a fixed DAG before invoking any semantic Agent reasoning.
Each step gates the next: an upstream failure invalidates downstream conclusions.

Order:
  0. Invalid eval case (question/reference/lineage)
  1. Route miss (retrieval should have fired but didn't)
  2. Document missing/wrong/conflicting (fact absent from pinned corpus)
  3. Retrieval recall miss (gold chunk not in top-30 candidates)
  4. Retrieval ranking miss (gold in top-30 but outside final top-k)
  5. Context noise / chunking issue (selected context insufficient)
  6. Generation issue (evidence present but unused or contradicted)
"""

from __future__ import annotations

from sales_agent.optimization.types import (
    CauseType,
    FailureDiagnosis,
    RecommendedAction,
)

# ── Ordered diagnosis rules ─────────────────────────────────────────────────
# Each rule: (cause, condition_fn(trace) → bool, action)

DIAGNOSIS_ORDER: list[tuple[CauseType, str, RecommendedAction]] = [
    ("invalid_eval_case", "eval_case_invalid", "fix_eval_case"),
    ("route_miss", "route_skipped_or_wrong", "update_router"),
    ("document_missing", "fact_absent_from_corpus", "add_document"),
    ("document_wrong", "fact_incorrect_in_corpus", "fix_document"),
    ("document_conflict", "conflicting_facts_in_corpus", "resolve_conflict"),
    ("retrieval_recall", "gold_not_in_top30", "update_retrieval_profile"),
    ("retrieval_ranking", "gold_in_top30_not_in_final", "update_retrieval_profile"),
    ("context_noise", "selected_context_noisy", "improve_chunking"),
    ("chunking_or_structure", "chunk_truncated_or_non_contained", "improve_chunking"),
    ("generation_issue", "evidence_unused_or_contradicted", "improve_generation"),
]


class FailureDiagnoser:
    """Deterministic failure attribution engine.

    The diagnoser is stateless: it receives trace evidence and returns a
    structured diagnosis. It never invokes an LLM or the production retriever.
    """

    def diagnose(self, trace: dict) -> FailureDiagnosis:
        """Attribute a failed case using ordered rule evaluation.

        Args:
            trace: A dict containing at minimum:
                - eval_case_valid (bool)
                - route_match (bool), expected_route, actual_route
                - fact_in_corpus (str): "present"|"absent"|"wrong"|"conflicting"
                - gold_in_candidates (bool): gold chunk in top-30?
                - gold_in_final (bool): gold chunk in final top-k?
                - context_selected (list[dict]): selected context chunks
                - gold_fact_in_answer (bool): did answer include gold fact?

        Returns:
            FailureDiagnosis with primary cause, evidence, and recommended action.
        """
        evidence: list[str] = []
        blocked: list[str] = []

        # 0. Invalid eval case
        if not trace.get("eval_case_valid", True):
            return self._build("invalid_eval_case", evidence, blocked, trace)

        # 1. Route miss
        if not trace.get("route_match", True):
            evidence.append(
                f"Expected route '{trace.get('expected_route')}', "
                f"got '{trace.get('actual_route')}'"
            )
            return self._build("route_miss", evidence, blocked, trace)
        blocked.append("route_miss")

        # 2. Document / fact presence
        fact_status = trace.get("fact_in_corpus", "present")
        if fact_status == "absent":
            evidence.append("Required fact not found in pinned knowledge version")
            return self._build("document_missing", evidence, blocked, trace)
        if fact_status == "wrong":
            evidence.append("Required fact is incorrect in pinned corpus")
            return self._build("document_wrong", evidence, blocked, trace)
        if fact_status == "conflicting":
            evidence.append("Conflicting versions of the fact exist in corpus")
            return self._build("document_conflict", evidence, blocked, trace)
        blocked.append("document_missing")

        # 3. Retrieval recall
        if not trace.get("gold_in_candidates", True):
            evidence.append("Gold chunk not found in retrieval candidate set (top-30)")
            return self._build("retrieval_recall", evidence, blocked, trace)
        blocked.append("retrieval_recall")

        # 4. Retrieval ranking
        if not trace.get("gold_in_final", True):
            evidence.append("Gold chunk in candidates but outside final top-k")
            return self._build("retrieval_ranking", evidence, blocked, trace)
        blocked.append("retrieval_ranking")

        # 5. Context noise / chunking
        context = trace.get("context_selected", [])
        if not context:
            evidence.append("No context was selected for generation")
            return self._build("context_noise", evidence, blocked, trace)

        # Check for truncated or non-self-contained context
        for chunk in context:
            text = chunk.get("text", "")
            if len(text) < 50:
                evidence.append(f"Chunk {chunk.get('chunk_id')} is truncated (<50 chars)")
                blocked.append("context_noise")
                return self._build("chunking_or_structure", evidence, blocked, trace)
        blocked.append("context_noise_chunking")

        # 6. Generation issue
        if not trace.get("gold_fact_in_answer", True):
            evidence.append("Sufficient evidence in context but not reflected in answer")
            return self._build("generation_issue", evidence, blocked, trace)

        # Default: human review for unclassified failures
        return self._build("generation_issue", evidence, blocked, trace)

    def _build(
        self,
        cause: CauseType,
        evidence: list[str],
        blocked: list[str],
        trace: dict,
    ) -> FailureDiagnosis:
        """Build a diagnosis from the first-matching rule."""
        action: RecommendedAction = "human_review"
        for ca, _, act in DIAGNOSIS_ORDER:
            if ca == cause:
                action = act
                break

        return FailureDiagnosis(
            primary_cause=cause,
            secondary_causes=[],
            confidence=0.85 if evidence else 0.5,
            evidence=evidence,
            blocked_checks=blocked,
            recommended_action=action,
            affected_case_ids=trace.get("case_ids", []),
        )
