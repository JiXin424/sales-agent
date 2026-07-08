"""Online sampling decisions (Spec 4 §3.5).

Pure, side-effect-free helpers that decide whether a completed conversation
thread should be retained for memory evaluation. The decision combines:

* an **inactivity gate** — a thread is only eligible once it has been idle for
  ``DEFAULT_INACTIVITY_SECONDS`` (30 min), i.e. the user has moved on and the
  turn is settled;
* a **high-risk override** — threads carrying a user correction, explicit
  negative feedback, or repeated clarification (≥ threshold) are ALWAYS
  retained regardless of the random sample, so failures are never silently
  dropped (§3.5);
* a **random sample** at ``DEFAULT_RATE`` (5%) for the remaining eligible
  threads, keeping the per-run cost bounded.

These functions are the unit-tested gate; :func:`run_online_sample` in the
runner wires them to a DB query and stores traces under restricted retention.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# §3.5: sample 5% of eligible completed threads.
DEFAULT_RATE = 0.05
# §3.5: a thread is eligible after 30 minutes of inactivity.
DEFAULT_INACTIVITY_SECONDS = 30 * 60
# §3.5: repeated clarification at or above this count is always retained.
DEFAULT_CLARIFICATION_REPEAT_THRESHOLD = 2


def is_high_risk(thread: dict[str, Any]) -> bool:
    """Whether a thread is always retained regardless of sampling (§3.5).

    True when the thread carries any of the high-risk signals: an explicit
    user correction, negative feedback, or clarification repeated at/above
    :data:`DEFAULT_CLARIFICATION_REPEAT_THRESHOLD`.
    """
    if thread.get("user_correction") or thread.get("negative_feedback"):
        return True
    if int(thread.get("clarification_attempts") or 0) >= DEFAULT_CLARIFICATION_REPEAT_THRESHOLD:
        return True
    return False


def should_sample(
    thread: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    rng: Optional[Callable[[], float]] = None,
    rate: float = DEFAULT_RATE,
    inactivity_seconds: int = DEFAULT_INACTIVITY_SECONDS,
) -> bool:
    """Decide whether to retain ``thread`` for online memory evaluation.

    Returns ``False`` when the thread is still within the inactivity window or
    has no recorded ``last_active_at``. Otherwise high-risk threads are always
    retained, and the rest are sampled at ``rate``. ``rng`` is an injectable
    ``random()``-style callable returning a float in ``[0, 1)`` so the decision
    is deterministic under test.
    """
    now = now or datetime.now(timezone.utc)
    rng = rng or random.random
    last_active = thread.get("last_active_at")
    if last_active is None:
        return False
    if (now - last_active).total_seconds() < inactivity_seconds:
        return False
    if is_high_risk(thread):
        return True
    return rng() < rate


__all__ = [
    "DEFAULT_CLARIFICATION_REPEAT_THRESHOLD",
    "DEFAULT_INACTIVITY_SECONDS",
    "DEFAULT_RATE",
    "is_high_risk",
    "should_sample",
]
