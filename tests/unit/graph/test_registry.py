"""Tests for the Graph Registry.

Ontology retrieval steps are now called inline by ``retrieve_node``;
the ``ontology-retrieval`` subgraph entry has been removed from the registry.
"""
import pytest


class TestGraphRegistry:
    """The registry must contain exactly online and guided-flow.

    The legacy daily-eval graph must be absent until a real implementation exists.
    Ontology retrieval is now inline (no longer a separate subgraph).
    """

    def test_registry_contains_expected_keys(self):
        from sales_agent.graph.registry import GRAPH_REGISTRY

        assert set(GRAPH_REGISTRY.keys()) == {"online", "guided-flow"}

    def test_daily_eval_absent(self):
        from sales_agent.graph.registry import GRAPH_REGISTRY

        assert "daily-eval" not in GRAPH_REGISTRY

    def test_ontology_retrieval_absent(self):
        """ontology-retrieval subgraph was removed — steps are now inline."""
        from sales_agent.graph.registry import GRAPH_REGISTRY

        assert "ontology-retrieval" not in GRAPH_REGISTRY

    def test_each_entry_has_builder_and_name(self):
        from sales_agent.graph.registry import GRAPH_REGISTRY

        for graph_id, entry in GRAPH_REGISTRY.items():
            assert "builder" in entry, f"{graph_id} missing builder"
            assert "name" in entry, f"{graph_id} missing name"
            assert callable(entry["builder"]), f"{graph_id} builder not callable"

    def test_each_builder_returns_compilable_graph(self):
        """Build and compile each registered graph (no runtime required)."""
        from sales_agent.graph.registry import GRAPH_REGISTRY

        for graph_id, entry in GRAPH_REGISTRY.items():
            builder = entry["builder"]()
            compiled = builder.compile()
            assert compiled is not None, f"{graph_id} compilation returned None"


class TestRegistryImportableFromGraphPackage:
    """The registry must also be importable from sales_agent.graph directly."""

    def test_registry_in_graph_init(self):
        from sales_agent.graph import GRAPH_REGISTRY

        assert "online" in GRAPH_REGISTRY
        assert "guided-flow" in GRAPH_REGISTRY
        # ontology-retrieval was removed — steps are now inline in retrieve_node
