"""Evidence gate node — enforces knowledge policy between retrieval and generation.

The evidence gate checks the ``knowledge_policy`` field set by the Evidence
Router (or precomputed route) and decides whether to block generation when
required evidence was not retrieved:

- ``required`` + no accepted sources → block generation, return
  evidence-insufficient answer.
- ``required`` + sources present → allow generation (no-op).
- ``optional`` (any sources) → allow generation (no-op).
- ``none`` (skip retrieval) → allow generation (no-op).
- Missing policy → backward-compatible no-op.
- Already skipped (``skip_generation=True``, e.g. ontology answer) → no-op.

When blocking, the node sets::

    {
        "skip_generation": True,
        "answer_dict": {"summary": "当前知识库中没有找到足够依据，我不能确认这项企业信息。",
                        "sections": []},
        "path_reason": "required_evidence_missing",
    }
"""

from __future__ import annotations

import logging

from sales_agent.graph.chat.state import ChatGraphState

logger = logging.getLogger(__name__)

_INSUFFICIENT_ANSWER = {
    "summary": "当前知识库中没有找到足够依据，我不能确认这项企业信息。",
    "sections": [],
}


def evidence_gate(state: ChatGraphState) -> dict:
    """Check whether generation should proceed based on evidence policy.

    Args:
        state: Current graph state with ``knowledge_policy`` and ``sources``.

    Returns:
        Empty dict (``{}``) when generation should proceed, or a partial
        state update blocking generation when required evidence is missing.
    """
    # If generation is already skipped (e.g. ontology subgraph answered),
    # pass through without further checks.
    if state.get("skip_generation"):
        logger.debug("evidence_gate: skip_generation already True, passing through")
        return {}

    policy = state.get("knowledge_policy")
    sources = state.get("sources", [])

    # Missing policy — backward compatible, allow generation
    if policy is None:
        return {}

    # ``none`` and ``optional`` always allow generation
    if policy in ("none", "optional"):
        logger.debug(
            "evidence_gate: policy=%s, allowing generation (%d sources)",
            policy,
            len(sources),
        )
        return {}

    # ``required`` — check that sources are present
    if policy == "required":
        if len(sources) == 0:
            logger.info(
                "evidence_gate: required policy but no sources — blocking generation"
            )
            return {
                "skip_generation": True,
                "answer_dict": _INSUFFICIENT_ANSWER,
                "path_reason": "required_evidence_missing",
            }
        logger.debug(
            "evidence_gate: required policy with %d sources, allowing generation",
            len(sources),
        )
        return {}

    # Unknown policy — safe default
    logger.warning("evidence_gate: unknown policy '%s', allowing generation", policy)
    return {}
