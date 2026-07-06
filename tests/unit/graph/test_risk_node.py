"""Tests for risk checking node."""
import pytest
from unittest.mock import MagicMock
from sales_agent.graph.nodes.risk_check import risk_check_node
from sales_agent.graph.edges.risk_conditions import check_risk_result
from sales_agent.graph.state import ChatGraphState


@pytest.fixture
def mock_runtime():
    """Runtime with no chat_model — LLM risk layer disabled (flag off)."""
    runtime = MagicMock()
    runtime.context = {"chat_model": None, "db": None}
    return runtime


@pytest.mark.asyncio
async def test_risk_node_clean_answer(mock_runtime):
    """Clean answer passes risk check."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "正常问题", "conversation_id": "c1", "channel": "local",
        "answer_dict": {"summary": "这是一个正常的回答", "sections": []},
        "sources": [],
    }
    result = await risk_check_node(state, mock_runtime)
    assert result["risk_action"] == "allow"
    assert result["input_risk_level"] == "none"


@pytest.mark.asyncio
async def test_risk_node_blocks_price_guarantee(mock_runtime):
    """Answer with price guarantee triggers block."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "问价格", "conversation_id": "c1", "channel": "local",
        "answer_dict": {
            "summary": "我们保证最低价，这个优惠是限时的",
            "sections": [{"title": "折扣", "content": "一定可以申请到最低折扣"}],
        },
        "sources": [],
    }
    result = await risk_check_node(state, mock_runtime)
    # Risk check should flag the answer
    assert result["risk_action"] in ("block", "warn", "rewrite")
    assert result["risk_result"] is not None


def test_check_risk_result_pass():
    """check_risk_result returns 'pass' for allow action."""
    assert check_risk_result({"risk_action": "allow"}) == "pass"


def test_check_risk_result_block():
    """check_risk_result returns 'block' for block action."""
    assert check_risk_result({"risk_action": "block", "retry_count": 0}) == "block"


def test_check_risk_result_max_retries():
    """check_risk_result returns 'max_retries' after 3 attempts."""
    assert check_risk_result({"risk_action": "block", "retry_count": 3}) == "max_retries"
