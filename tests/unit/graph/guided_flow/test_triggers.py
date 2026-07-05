import pytest

from sales_agent.graph.guided_flow.triggers import (
    is_cancel_command,
    resolve_requested_flow,
)


@pytest.mark.parametrize(
    ("text", "flow_id"),
    [
        ("访前准备", "visit_preparation"),
        ("访后复盘", "post_visit_review"),
        ("小赢欣赏", "small_win_appreciation"),
        ("卡点破框", "sales_block_breakthrough"),
    ],
)
def test_exact_phrases_trigger_flow(text, flow_id):
    assert resolve_requested_flow(message=f"  {text}  ", entry_action=None) == flow_id


def test_entry_action_has_priority():
    assert resolve_requested_flow(
        message="小赢欣赏", entry_action="post_visit_review"
    ) == "post_visit_review"


def test_substrings_do_not_trigger():
    assert resolve_requested_flow(
        message="明天访前准备材料还没写完", entry_action=None
    ) is None


def test_cancel_is_exact():
    assert is_cancel_command("退出") is True
    assert is_cancel_command("我不想退出客户沟通") is False
