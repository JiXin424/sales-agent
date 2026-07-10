import pytest

OBSERVE_TEST_CASES = [
    # (reply, success_criteria, expected_tag, expected_met_signal)
    ("约到了，下周三下午3点", "张总确认技术负责人和可沟通时间", "achieved", True),
    ("他回了消息说知道了但没给具体时间", "张总确认技术负责人和可沟通时间", "partial", False),
    ("他说最近预算冻结，暂时推不动", "张总确认技术负责人和可沟通时间", "new_obstacle", False),
    ("好的，收到", "张总确认技术负责人和可沟通时间", None, False),  # no meaningful signal
    ("预算冻结到Q4，要等下一轮预算", "约到采购负责人", "new_obstacle", False),
]


@pytest.mark.parametrize("reply,criteria,expected_tag,expected_met_signal", OBSERVE_TEST_CASES)
def test_observe_fallback_classification(reply, criteria, expected_tag, expected_met_signal):
    """The keyword-heuristic fallback should classify correctly even without LLM."""
    from sales_agent.services.sales_actions.parser import _fallback_outcome
    result = _fallback_outcome(reply)
    if expected_tag is not None:
        assert result.outcome_tag == expected_tag
    assert result.met_signal == expected_met_signal
