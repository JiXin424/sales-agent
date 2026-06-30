"""Integration test: Phase 1 graph produces equivalent output to ChatPipeline."""
import pytest
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.state import ChatGraphState


@pytest.mark.asyncio
async def test_graph_fast_command_help():
    """Graph handles '帮助' command same as ChatPipeline."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "帮助",
            "conversation_id": "conv-test-001",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-help"}},
        context={"db": None},
    )

    assert result["path"] == "fast"
    assert "answer_dict" in result
    assert "你可以直接问销售问题" in result["answer_dict"]["summary"]


@pytest.mark.asyncio
async def test_graph_normal_message_routes():
    """Graph routes a normal sales message and generates answer."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "客户说太贵了怎么回",
            "conversation_id": "conv-test-002",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-normal"}},
        context={"db": None},
    )

    assert result["task_type"] == "objection_handling"
    assert "answer_dict" in result
    assert "summary" in result["answer_dict"]


@pytest.mark.asyncio
async def test_graph_reset_generates_new_conversation_id():
    """Graph generates new conversation_id on reset."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "新话题",
            "conversation_id": "conv-old-003",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-reset"}},
        context={"db": None},
    )

    assert result["conversation_id"] != "conv-old-003"
