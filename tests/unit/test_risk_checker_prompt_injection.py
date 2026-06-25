"""验证 risk_checker.check_llm_risk 支持外部注入 risk prompt（阶段2 解耦）。"""

import pytest

from sales_agent.services.risk_checker import RiskChecker


class _FakeChatModel:
    def __init__(self, response: str):
        self._response = response
        self.received_messages: list[dict] | None = None

    async def generate(self, messages, **kwargs):
        self.received_messages = messages
        return self._response


@pytest.mark.asyncio
async def test_check_llm_risk_uses_injected_prompt():
    """传入 risk_prompt 时，渲染基于该模板。"""
    custom = "CUSTOMRISK msg={message} ans={answer}"
    model = _FakeChatModel(
        '{"level":"high","flags":["price_commitment"],"action":"block","notice":"x"}'
    )
    checker = RiskChecker()
    result = await checker.check_llm_risk("m1", "a1", model, risk_prompt=custom)
    assert result.action == "block"
    user_content = model.received_messages[0]["content"]
    assert "CUSTOMRISK" in user_content
    assert "m1" in user_content
    assert "a1" in user_content


@pytest.mark.asyncio
async def test_check_llm_risk_falls_back_to_default():
    """未传 risk_prompt 时回退到内置默认。"""
    model = _FakeChatModel(
        '{"level":"none","flags":[],"action":"allow","notice":""}'
    )
    checker = RiskChecker()
    result = await checker.check_llm_risk("m", "a", model)
    assert result.action == "allow"
    assert "合规检查员" in model.received_messages[0]["content"]
