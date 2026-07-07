"""Tests for the ChatPipeline StateGraph."""
import pytest
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.state import ChatGraphState
from langgraph.graph.state import StateGraph


def test_build_chat_graph_returns_state_graph():
    """build_chat_graph returns an un-compiled StateGraph builder."""
    builder = build_chat_graph()
    assert isinstance(builder, StateGraph)


def test_graph_compiles_without_checkpointer():
    """Graph compiles successfully without a checkpointer (for testing)."""
    builder = build_chat_graph()
    graph = builder.compile()
    assert graph is not None


def test_graph_compiles_with_memory_checkpointer():
    """Graph compiles with InMemorySaver for unit testing."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_invokes_minimal_input():
    """Graph processes minimal input through basic pipeline."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "帮助",
            "conversation_id": "conv-001",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-1"}},
        context={"db": None},
    )
    # After invoke, state passes through the pipeline
    assert result is not None
    assert result["message"] == "帮助"
    assert result["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_parallel_retrieve_writes_ontology_context_text_without_crash():
    """并行 fan-out 两路都返回 ``ontology_context_text`` 时不崩。

    回归 commit: 给 ontology_context_text/retrieval_result 加 _reduce_coalesce
    reducer 前，rag 路 web_fallback 与 ontology 路同时写该字段会抛
    InvalidUpdateError。本测试 spy retrieve_node 让两路都写该字段，
    验证 reducer 合并成功、turn 完成且 sources 合并。
    """
    from langgraph.checkpoint.memory import InMemorySaver
    import sales_agent.graph.nodes.retrieval as rmod
    import sales_agent.graph.chat_graph as cgmod
    from sales_agent.graph.edges.path_conditions import select_retrieval_path
    from langgraph.types import Send

    calls = []
    orig_retrieve = rmod.retrieve_node

    async def spy(state, runtime):
        path = state.get("retrieval_path", "skip")
        calls.append(path)
        # 两路都写 ontology_context_text —— 模拟 ontology 路正常 + rag 路 web_fallback
        if path == "ontology":
            return {
                "retrieval_info": {"called": True, "path": path},
                "sources": [{"title": "onto", "source_type": "ontology"}],
                "skip_generation": False,
                "ontology_context_text": "## 本体检索结果",
            }
        return {
            "retrieval_info": {"called": True, "path": path},
            "sources": [{"title": "rag-web", "source_type": "web"}],
            "retrieval_result": None,
            "skip_generation": False,
            "ontology_context_text": "## 联网搜索分析",  # rag web_fallback 也写
        }

    # 强制 select_retrieval_path 走 fan-out（绕过真实 settings 的 neo4j 配置依赖）
    def force_fanout(state):
        ctx = {
            "tenant_id": state["tenant_id"], "message": state["message"],
            "task_type": state.get("task_type", "knowledge_qa"),
            "agent_id": state.get("agent_id"),
        }
        return [
            Send("retrieve", {**ctx, "retrieval_path": "ontology"}),
            Send("retrieve", {**ctx, "retrieval_path": "rag"}),
        ]

    # patch 两处引用（chat_graph 顶层 from import 绑定了 retrieve_node 名字）
    rmod.retrieve_node = spy
    cgmod.retrieve_node = spy
    mp = pytest.MonkeyPatch()
    mp.setattr("sales_agent.graph.chat_graph.select_retrieval_path", force_fanout)
    try:
        builder = build_chat_graph()
        graph = builder.compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            {
                "tenant_id": "t1", "user_id": "u1",
                "message": "test", "conversation_id": "c1", "channel": "local",
                "needs_retrieval": True, "knowledge_policy": "required",
                "task_type": "knowledge_qa", "agent_id": "a1",
                "precomputed_route": True,
            },
            config={"configurable": {"thread_id": "t1"}},
            context={"db": None},
        )
    finally:
        rmod.retrieve_node = orig_retrieve
        cgmod.retrieve_node = orig_retrieve
        mp.undo()

    # 两路都被调用
    assert sorted(calls) == ["ontology", "rag"], f"calls={calls}"
    # turn 完成无 InvalidUpdateError
    assert result is not None
    # sources 经 add reducer 合并（ontology + rag-web）
    assert len(result["sources"]) == 2
    titles = {s["title"] for s in result["sources"]}
    assert titles == {"onto", "rag-web"}
    # ontology_context_text 经 _reduce_coalesce 保留非空值（ontology 优先）
    assert result["ontology_context_text"] == "## 本体检索结果"
