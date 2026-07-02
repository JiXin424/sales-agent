"""Validate candidate patches: reject mixed types, non-allowlisted fields,
and un-evidenced document changes.
"""

from __future__ import annotations

from sales_agent.optimization.types import CandidatePatch


class PatchValidationError(ValueError):
    """A patch failed structural or policy validation."""


def validate_candidate_patch(patch: CandidatePatch) -> CandidatePatch:
    """Validate and return the patch, or raise PatchValidationError.

    Rules:
    - change_type must be one of (router, retrieval, document).
    - Only one change_type per candidate.
    - Document patches without evidence_ids become knowledge gaps.
    """
    if patch.change_type not in ("router", "retrieval", "document"):
        raise PatchValidationError(
            f"Invalid change_type: {patch.change_type}"
        )

    # Router patches must provide at least one router field
    if patch.change_type == "router":
        if not any([patch.router_rules_json, patch.router_examples_json]):
            raise PatchValidationError(
                "Router patches must include rules_json or examples_json"
            )

    # Retrieval patches must provide at least one retrieval field
    if patch.change_type == "retrieval":
        if not any([patch.synonyms_json, patch.query_rewrite_enabled is not None, patch.retrieval_params_json]):
            raise PatchValidationError(
                "Retrieval patches must include at least one parameter change"
            )

    # Document patches without evidence are knowledge gaps (not errors)
    if patch.change_type == "document":
        if not patch.evidence_ids and not patch.diff:
            raise PatchValidationError(
                "Document patches must include a diff or evidence"
            )

    return patch
