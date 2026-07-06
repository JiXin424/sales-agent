"""验证 task_router 支持外部注入 router prompt（阶段2 解耦）。"""

import pytest

from sales_agent.services.task_router import _llm_route


class _FakeChatModel:
    def __init__(self, response: str):
        self._response = response
        self.received_messages: list[dict] | None = None

    async def generate(self, messages, **kwargs):
        self.received_messages = messages
        return self._response


@pytest.mark.asyncio
async def test_llm_route_uses_injected_router_prompt():
    """传入 router_prompt 时，LLM 收到的 user 消息应基于该模板而非内置默认。"""
    custom = "CUSTOMROUTER [{message}]"
    model = _FakeChatModel(
        '{"task_type":"knowledge_qa","confidence":0.9,"needs_retrieval":true}'
    )
    result = await _llm_route("hello", model, router_prompt=custom)
    assert result is not None
    assert result.task_type == "knowledge_qa"
    user_content = model.received_messages[0]["content"]
    assert "CUSTOMROUTER" in user_content
    assert "hello" in user_content


@pytest.mark.asyncio
async def test_llm_route_falls_back_to_default_when_no_prompt():
    """未传 router_prompt 时回退到内置默认。"""
    model = _FakeChatModel(
        '{"task_type":"general_sales_coaching","confidence":0.5,"needs_retrieval":false}'
    )
    result = await _llm_route("随便说点什么", model)
    assert result is not None
    user_content = model.received_messages[0]["content"]
    assert "emotional_support" in user_content  # 内置默认含 12 类列表
    assert "knowledge_qa" in user_content
