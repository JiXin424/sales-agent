"""promote-trace workflow (Spec 4 §9).

Turns a sampled production trace into a minimal, anonymized regression scenario
with an explicit expected state/outcome. The pure functions here are the
unit-tested gate; :func:`run_promote_trace` in ``runner.py`` is the DB-backed
mode that persists a ``PromotedRegression`` (status ``draft``).

* §9.2 — anonymization hashes the scope, drops outbound replies, and removes the
  raw conversation dump. The per-turn user ``input`` is RETAINED verbatim in the
  draft so a reviewer can reproduce the failure (§9.5); it MUST be
  human-anonymized during the draft → reviewed → committed review flow (§9)
  before the scenario enters a committed dataset. ``validate_dataset`` at
  promote-time catches STRUCTURED identifiers (phone/email/id/secret) but NOT
  free-text PII (names, addresses) — detecting free-text PII is the human
  reviewer's job, not the automated gate's.
* §9.3 — the trace is classified into one of the §9.3 root-cause categories.
* §9.4 — a minimal regression scenario with explicit expected is produced and
  MUST pass ``validate_dataset`` before it can be committed to the suite.
"""
from __future__ import annotations

import copy
from typing import Any

from eval.memory_eval.schema import (
    ExpectedTurn,
    FinalExpected,
    MultiturnScenario,
    ScenarioTurn,
)

# §9.3 root-cause categories.
_ROOT_CATEGORIES = (
    "routing",
    "topic",
    "memory",
    "recall",
    "profile",
    "rag",
    "model",
    "rendering",
    "infrastructure",
)


def anonymize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Hash the scope and drop outbound replies (§9.2).

    The scope identifiers (tenant/agent/user/thread) are already hashed upstream
    in ``build_eval_trace``. This function drops outbound replies
    (``reply`` / ``outbound``) and any raw conversation dump.

    The per-turn user ``input`` is RETAINED verbatim — NOT redacted — so that a
    reviewer can reproduce the failure (§9.5 reproducibility). Redacting the
    input would break reproduction. The retained ``input`` MUST be
    human-anonymized during the draft → reviewed → committed review flow (§9)
    before the scenario enters a committed dataset.

    :func:`validate_dataset` (run at promote-time in ``runner.py``) catches
    STRUCTURED identifiers (phone/email/id/secret) but NOT free-text PII such as
    names or addresses embedded in natural-language utterances — detecting
    free-text PII is the human reviewer's responsibility, not this function's.
    """
    out = copy.deepcopy(trace)
    for t in out.get("turns", []):
        t.pop("reply", None)
        t.pop("outbound", None)
    out.pop("raw_conversation", None)
    return out


def classify_root_cause(trace: dict[str, Any]) -> str:
    """Map a trace to a §9.3 root-cause category.

    Priority mirrors the §9 failure taxonomy: a memory degradation (write/recall
    exception or flagged degradation) dominates, then routing (a topic switch
    with no memory selected), then a recall miss (user correction), then an
    ambiguous topic transition. Falls back to ``model`` for residual issues.
    """
    if trace.get("memory_degraded") or trace.get("memory_degradation_reason"):
        return "memory"
    if not trace.get("selected_memory_ids") and trace.get("topic_transition") in ("switch", "new"):
        return "routing"
    if trace.get("signals", {}).get("user_correction"):
        return "recall"
    if trace.get("topic_transition") == "ambiguous":
        return "topic"
    return "model"


def build_regression_scenario(
    trace: dict[str, Any], *, scenario_id: str, expected: dict[str, Any]
) -> MultiturnScenario:
    """Build a minimal anonymized regression scenario with explicit expected (§9.4).

    The last turn carries the caller-supplied ``expected`` state/outcome (e.g.
    ``{"turn_relation": "switch"}``); earlier turns get a neutral
    :class:`ExpectedTurn`. The scenario is tagged with the classified root cause
    so the regression suite can group promoted scenarios by failure category.

    The returned scenario is constructed to pass :func:`validate_dataset` for
    STRUCTURED identifiers (phone/email/id/secret). The per-turn ``input`` is
    retained verbatim from the trace (so a reviewer can reproduce the failure,
    §9.5) and is NOT redacted here — free-text PII in those inputs MUST be
    scrubbed by a human reviewer during the draft → reviewed → committed review
    flow (§9) before the scenario enters a committed dataset.
    """
    raw_turns = trace.get("turns") or [{"input": "<redacted>"}]
    last_index = len(raw_turns) - 1
    turns: list[ScenarioTurn] = []
    for i, t in enumerate(raw_turns):
        turns.append(
            ScenarioTurn(
                input=t.get("input", "<redacted>"),
                event_id=t.get("event_id") or f"{scenario_id}-{i}",
                expected=(
                    ExpectedTurn(**expected) if i == last_index else ExpectedTurn()
                ),
            )
        )
    return MultiturnScenario(
        id=scenario_id,
        version=1,
        tags=["promoted", classify_root_cause(trace)],
        turns=turns,
        final_expected=FinalExpected(),
    )


__all__ = ["anonymize_trace", "build_regression_scenario", "classify_root_cause"]
