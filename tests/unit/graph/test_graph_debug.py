"""Tests for graph_debug helpers: node/edge counts + subgraph-node highlighting.

Regression guard for two bugs fixed together:
  1. ``_count_nodes_edges`` parsed mermaid lines requiring both ``(`` and ``[``,
     but ordinary node lines are ``name(name)`` (only ``(``). Only
     ``__start__``/``__end__`` lines like ``([<p>…</p>])`` matched → every graph
     reported ``node_count == 2``. Fixed by reading ``len(g.nodes)``/``len(g.edges)``
     from the compiled graph object directly.
  2. Subgraph-entry nodes (``chat``, ``guided_flow`` in the online graph) were
     visually indistinguishable from ordinary nodes. Fixed by appending a
     ``subgraphNode`` mermaid class (orange thick border) for nodes that are
     compiled subgraphs or whose id matches a GRAPH_REGISTRY key.
"""

import pytest

from sales_agent.api.routes.graph_debug import (
    _annotate_node_labels,
    _build_node_infos,
    _decorate_mermaid,
    _identify_llm_nodes,
    _identify_subgraph_nodes,
    _normalize_id,
)
from sales_agent.graph.registry import GRAPH_REGISTRY


def _graph(graph_id: str):
    """Compile ``graph_id`` from the registry and return its underlying graph view."""
    return GRAPH_REGISTRY[graph_id]["builder"]().compile().get_graph()


class TestNodeEdgeCounts:
    """Counts must come from the graph object, not mermaid text parsing."""

    @pytest.mark.parametrize(
        "graph_id,expected_nodes,expected_edges",
        [
            ("online", 11, 15),
            ("guided-flow", 5, 6),
            ("chat", 12, 14),
        ],
    )
    def test_counts_match_graph_object(self, graph_id, expected_nodes, expected_edges):
        g = _graph(graph_id)
        assert len(g.nodes) == expected_nodes
        assert len(g.edges) == expected_edges

    def test_online_node_count_not_stuck_at_two(self):
        """Regression: every graph used to report node_count=2. online has 11."""
        g = _graph("online")
        assert len(g.nodes) != 2
        assert len(g.nodes) == 11


class TestIdentifySubgraphNodes:
    def test_online_has_chat_and_guided_flow(self):
        ids = set(_identify_subgraph_nodes(_graph("online")))
        assert ids == {"chat", "guided_flow"}

    def test_chat_graph_has_none(self):
        assert _identify_subgraph_nodes(_graph("chat")) == []

    def test_guided_flow_graph_has_none(self):
        assert _identify_subgraph_nodes(_graph("guided-flow")) == []

    def test_excludes_start_end(self):
        ids = _identify_subgraph_nodes(_graph("online"))
        assert "__start__" not in ids
        assert "__end__" not in ids


class TestDecorateMermaid:
    def test_appends_class_and_classdef_for_single_subgraph_node(self):
        mermaid = "graph TD;\n\tchat(chat)\n"
        out = _decorate_mermaid(mermaid, ["chat"])
        assert "class chat subgraphNode" in out
        assert "classDef subgraphNode" in out
        assert "#ff9800" in out  # orange stroke
        assert out.startswith(mermaid)

    def test_multiple_nodes_appear_in_class_clause(self):
        out = _decorate_mermaid("graph TD;\n", ["chat", "guided_flow"])
        assert "chat" in out and "guided_flow" in out
        assert "subgraphNode" in out

    def test_no_subgraph_nodes_returns_unchanged(self):
        mermaid = "graph TD;\n\tfoo(foo)\n"
        assert _decorate_mermaid(mermaid, []) == mermaid

    def test_online_mermaid_highlights_its_subgraph_nodes(self):
        """End-to-end: online diagram highlights chat + guided_flow."""
        g = _graph("online")
        mermaid = _decorate_mermaid(g.draw_mermaid(), _identify_subgraph_nodes(g))
        assert "classDef subgraphNode" in mermaid
        assert "chat" in mermaid and "guided_flow" in mermaid

    def test_chat_mermaid_has_no_highlight(self):
        """chat's own nodes are ordinary — no subgraph highlighting."""
        g = _graph("chat")
        mermaid = _decorate_mermaid(g.draw_mermaid(), _identify_subgraph_nodes(g))
        assert "subgraphNode" not in mermaid


class TestNormalizeId:
    def test_hyphen_equals_underscore(self):
        assert _normalize_id("guided-flow") == "guided_flow"
        assert _normalize_id("guided_flow") == "guided_flow"
        assert _normalize_id("chat") == "chat"


class TestAnnotateNodeLabels:
    """普通节点 label 加中文小字功能说明。"""

    def test_annotates_plain_node(self):
        mermaid = "graph TD;\n\tnormalize_turn(normalize_turn)\n"
        out = _annotate_node_labels(mermaid, skip_nodes=set(), graph_id="online")
        assert 'normalize_turn("normalize_turn<br/>' in out
        # desc 取自 node_metadata（不再硬编码文案，只校验注解已注入）
        assert "标准化" not in out or "路由" in out
        assert "<font size='2' color='#888'>" in out

    def test_skips_start_end(self):
        mermaid = "graph TD;\n\t__start__([<p>__start__</p>]):::first\n\tfoo(foo)\n"
        out = _annotate_node_labels(mermaid, skip_nodes=set(), graph_id="online")
        # __start__ 行原样保留(格式不同,正则不匹配)
        assert "__start__([<p>__start__</p>]):::first" in out
        assert "<br/>" not in out.split("__start__")[0]

    def test_skips_subgraph_nodes(self):
        """子图入口节点(如 online 的 chat)不加注解——它本身是子图,不是普通节点。"""
        mermaid = "graph TD;\n\tchat(chat)\n\tnormalize_turn(normalize_turn)\n"
        out = _annotate_node_labels(mermaid, skip_nodes={"chat"}, graph_id="online")
        # chat 行原样保留
        assert "chat(chat)" in out
        assert "chat<br/>" not in out
        # 普通节点仍加注解
        assert "normalize_turn<br/>" in out

    def test_skips_unknown_node(self):
        """不在 node_metadata 里的节点不加注解。"""
        mermaid = "graph TD;\n\tweird_node(weird_node)\n"
        out = _annotate_node_labels(mermaid, skip_nodes=set(), graph_id="online")
        assert out == mermaid  # 原样返回

    def test_does_not_touch_edge_lines(self):
        """边行 (a --> b) 不应被改动。"""
        mermaid = "graph TD;\n\tfoo(foo)\n\tfoo --> bar;\n"
        out = _annotate_node_labels(mermaid, skip_nodes=set(), graph_id="chat")
        # foo 不在元数据,原样;边行也不变
        # 用一个在元数据的节点验证边不被碰
        mermaid2 = "graph TD;\n\tlog(log)\n\tlog --> __end__;\n"
        out2 = _annotate_node_labels(mermaid2, skip_nodes=set(), graph_id="chat")
        assert "log --> __end__;" in out2  # 边行原样
        assert 'log("log<br/>' in out2  # 节点行已注解

    def test_online_annotates_seven_plain_nodes(self):
        """online 图 7 个普通节点加注解,2 个子图节点跳过。"""
        g = _graph("online")
        mermaid = g.draw_mermaid()
        subgraph = set(_identify_subgraph_nodes(g))
        out = _annotate_node_labels(mermaid, subgraph, graph_id="online")
        assert out.count("<font size='2'") == 7
        # 子图节点未加注解
        assert "guided_flow<br/>" not in out
        assert "chat<br/>" not in out

    def test_guided_flow_annotates_three(self):
        out = _annotate_node_labels(_graph("guided-flow").draw_mermaid(), set(), graph_id="guided-flow")
        assert out.count("<font size='2'") == 3

    def test_chat_annotates_ten(self):
        out = _annotate_node_labels(_graph("chat").draw_mermaid(), set(), graph_id="chat")
        assert out.count("<font size='2'") == 10


class TestLLMNodeHighlight:
    """LLM 节点蓝色高亮 + node_metadata 驱动的节点元数据。"""

    def test_chat_identifies_four_llm_nodes(self):
        """chat 图 4 个 LLM 节点：route_task/retrieve/generate/check_risk。"""
        llm = _identify_llm_nodes("chat", _graph("chat"))
        assert set(llm) == {"route_task", "retrieve", "generate", "check_risk"}

    def test_online_identifies_two_llm_nodes(self):
        """online 图 2 个 LLM 节点：context_resolution/evidence_routing（子图节点不算）。"""
        llm = _identify_llm_nodes("online", _graph("online"))
        assert set(llm) == {"context_resolution", "evidence_routing"}

    def test_guided_flow_identifies_advance_flow(self):
        """guided-flow 图 1 个 LLM 节点：advance_flow。"""
        llm = _identify_llm_nodes("guided-flow", _graph("guided-flow"))
        assert llm == ["advance_flow"]

    def test_decorate_mermaid_appends_llm_class(self):
        """LLM 节点高亮：mermaid 末尾追加 llmNode class + classDef。"""
        g = _graph("chat")
        m = _decorate_mermaid(g.draw_mermaid(), [], _identify_llm_nodes("chat", g))
        assert "class route_task,retrieve,generate,check_risk llmNode" in m
        assert "classDef llmNode" in m

    def test_decorate_mermaid_subgraph_and_llm_coexist(self):
        """子图节点（橙）和 LLM 节点（蓝）可同时出现，互不干扰。"""
        g = _graph("online")
        sub = _identify_subgraph_nodes(g)
        llm = _identify_llm_nodes("online", g)
        m = _decorate_mermaid(g.draw_mermaid(), sub, llm)
        assert "subgraphNode" in m
        assert "llmNode" in m


class TestNodeInfosAndPromptMap:
    """结构化 nodes 字段 + 节点↔prompt 映射表。"""

    def test_chat_node_infos_count(self):
        infos, _ = _build_node_infos("chat", _graph("chat"))
        assert len(infos) == 10  # 10 个 add_node

    def test_generate_node_has_multiple_prompts(self):
        """generate 节点对多 prompt（system + 12 task 分派）。"""
        infos, _ = _build_node_infos("chat", _graph("chat"))
        gen = next(n for n in infos if n.id == "generate")
        assert gen.calls_llm is True
        assert len(gen.prompts) >= 2

    def test_pure_function_node_has_no_prompts(self):
        """纯函数节点（validate）无 prompt，prompt_name 为「—」。"""
        _, pmap = _build_node_infos("chat", _graph("chat"))
        validate_rows = [r for r in pmap if r.node == "validate"]
        assert len(validate_rows) == 1
        assert validate_rows[0].calls_llm is False
        assert validate_rows[0].prompt_name == "—"

    def test_route_task_prompt_mapped(self):
        """route_task 节点接入 LLM 后对应 TASK_ROUTER_PROMPT。"""
        _, pmap = _build_node_infos("chat", _graph("chat"))
        rt_rows = [r for r in pmap if r.node == "route_task"]
        assert len(rt_rows) == 1
        assert rt_rows[0].calls_llm is True
        assert "TASK_ROUTER" in rt_rows[0].prompt_name

    def test_check_risk_prompt_mapped(self):
        """check_risk 节点接入 LLM 后对应 RISK_CHECK_PROMPT。"""
        _, pmap = _build_node_infos("chat", _graph("chat"))
        risk_rows = [r for r in pmap if r.node == "check_risk"]
        assert len(risk_rows) == 1
        assert risk_rows[0].calls_llm is True
        assert "RISK_CHECK" in risk_rows[0].prompt_name

    def test_subgraph_node_typed_subgraph(self):
        """online 图的 chat/guided_flow 节点 type=subgraph。"""
        infos, _ = _build_node_infos("online", _graph("online"))
        for nid in ("chat", "guided_flow"):
            node = next(n for n in infos if n.id == nid)
            assert node.type == "subgraph"
