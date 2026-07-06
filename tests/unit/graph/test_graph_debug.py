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
    _decorate_mermaid,
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
