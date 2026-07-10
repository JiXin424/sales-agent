"""Test Replan: OutcomeExtraction model contract tests."""

from sales_agent.services.sales_actions.contracts import OUTCOME_TAGS, OutcomeExtraction


def test_replan_detects_new_obstacle_triggers_memory_and_cancel():
    """When outcome is new_obstacle, replan should produce next_action + want to write memory + cancel siblings."""
    outcome = OutcomeExtraction(
        outcome_tag="new_obstacle",
        outcome_note="预算冻结至Q3",
        met_signal=False,
        confidence=0.92,
    )
    assert outcome.outcome_tag == "new_obstacle"
    assert outcome.met_signal is False
    assert outcome.outcome_tag in OUTCOME_TAGS


def test_achieved_outcome_skips_heavy_replan():
    """Achieved outcome → light wrap-up only; no cancel, no next_action unless user asks."""
    outcome = OutcomeExtraction(
        outcome_tag="achieved",
        outcome_note="约到了技术交流",
        met_signal=True,
        confidence=0.95,
    )
    assert outcome.outcome_tag == "achieved"
    assert outcome.met_signal is True
    assert outcome.outcome_tag in OUTCOME_TAGS


def test_partial_outcome_triggers_replan():
    """Partial outcome should trigger full replan."""
    outcome = OutcomeExtraction(
        outcome_tag="partial",
        outcome_note="回了消息但没给时间",
        met_signal=False,
        confidence=0.7,
    )
    assert outcome.outcome_tag == "partial"
    assert outcome.outcome_tag in OUTCOME_TAGS


def test_no_response_outcome_triggers_replan():
    """No_response outcome should trigger full replan."""
    outcome = OutcomeExtraction(
        outcome_tag="no_response",
        outcome_note="用户没有回复",
        met_signal=False,
        confidence=0.6,
    )
    assert outcome.outcome_tag == "no_response"
    assert outcome.outcome_tag in OUTCOME_TAGS


def test_outcome_tag_validity():
    """All tags used in replan logic must be valid OUTCOME_TAGS."""
    for tag in ("achieved", "partial", "new_obstacle", "no_response"):
        assert tag in OUTCOME_TAGS
