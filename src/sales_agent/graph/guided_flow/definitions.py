"""Guided flow registry — maps flow IDs to ``FlowDefinition`` instances.

Each definition pairs a synchronous start function with an async advance
function, both imported from the handler modules.  The registry is the
single source of truth for the graph nodes.
"""

from __future__ import annotations

from sales_agent.graph.guided_flow.handlers.coach_flows import (
    advance_sales_block,
    advance_small_win,
    start_sales_block,
    start_small_win,
)
from sales_agent.graph.guided_flow.handlers.visit_flows import (
    advance_post_visit,
    advance_visit_preparation,
    start_post_visit,
    start_visit_preparation,
)
from sales_agent.graph.guided_flow.types import FlowDefinition

FLOW_DEFINITIONS: dict[str, FlowDefinition] = {
    "visit_preparation": FlowDefinition(
        id="visit_preparation",
        label="访前准备",
        trigger_phrases=frozenset({"访前准备"}),
        start=start_visit_preparation,
        advance=advance_visit_preparation,
    ),
    "post_visit_review": FlowDefinition(
        id="post_visit_review",
        label="访后复盘",
        trigger_phrases=frozenset({"访后复盘"}),
        start=start_post_visit,
        advance=advance_post_visit,
    ),
    "small_win_appreciation": FlowDefinition(
        id="small_win_appreciation",
        label="小赢欣赏",
        trigger_phrases=frozenset({"小赢欣赏"}),
        start=start_small_win,
        advance=advance_small_win,
    ),
    "sales_block_breakthrough": FlowDefinition(
        id="sales_block_breakthrough",
        label="卡点破框",
        trigger_phrases=frozenset({"卡点破框"}),
        start=start_sales_block,
        advance=advance_sales_block,
    ),
}


def get_flow_definition(flow_id: str) -> FlowDefinition:
    """Look up a ``FlowDefinition`` by *flow_id*, or raise ``ValueError``."""
    try:
        return FLOW_DEFINITIONS[flow_id]
    except KeyError:
        msg = f"unknown guided flow: {flow_id}"
        raise ValueError(msg) from None


__all__ = [
    "FLOW_DEFINITIONS",
    "get_flow_definition",
]
