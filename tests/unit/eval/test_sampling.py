from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eval.memory_eval.sampling import is_high_risk, should_sample


def _thread(last_active_ago_seconds, *, correction=False, negative=False, clarifications=0):
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    return {
        "last_active_at": now - timedelta(seconds=last_active_ago_seconds),
        "user_correction": correction,
        "negative_feedback": negative,
        "clarification_attempts": clarifications,
    }


def test_not_eligible_before_inactivity_window():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # 10 minutes idle → not yet eligible (30 min threshold)
    assert should_sample(_thread(600), now=now, rng=lambda: 0.0) is False


def test_high_risk_always_retained_regardless_of_sampling():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # eligible (40 min idle) + user correction → always retained
    assert should_sample(_thread(2400, correction=True), now=now, rng=lambda: 0.99) is True


def test_normal_thread_sampled_at_5_percent():
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    # eligible + rng below 0.05 → sampled
    assert should_sample(_thread(2400), now=now, rng=lambda: 0.03) is True
    # eligible + rng above 0.05 → not sampled
    assert should_sample(_thread(2400), now=now, rng=lambda: 0.10) is False


def test_is_high_risk_detection():
    assert is_high_risk(_thread(0, correction=True)) is True
    assert is_high_risk(_thread(0, negative=True)) is True
    assert is_high_risk(_thread(0, clarifications=2)) is True
    assert is_high_risk(_thread(0)) is False
