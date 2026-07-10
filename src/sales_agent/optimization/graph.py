"""Optimization LangGraph: resumable, checkpointed workflow.

Stages: baseline → diagnose → propose → build → targeted_eval →
regression_eval → awaiting_approval → publish → question_evolution.

Thread ID format: ``kbopt:{tenant}:{iteration}:{candidate}:{run}``
"""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from sales_agent.optimization.state import OptimizationState
from sales_agent.optimization import nodes


def build_optimization_graph() -> StateGraph:
    """Build the optimization workflow graph.

    Returns a compiled StateGraph that can be checkpointed and resumed.
    """
    workflow = StateGraph(OptimizationState)

    # Add nodes
    workflow.add_node("baseline", nodes.baseline_node)
    workflow.add_node("diagnose", nodes.diagnose_node)
    workflow.add_node("propose", nodes.propose_node)
    workflow.add_node("build", nodes.build_node)
    workflow.add_node("targeted_eval", nodes.targeted_eval_node)
    workflow.add_node("regression_eval", nodes.regression_eval_node)
    workflow.add_node("awaiting_approval", nodes.awaiting_approval_node)
    workflow.add_node("publish", nodes.publish_node)
    workflow.add_node("question_evolution", nodes.question_evolution_node)

    # Set entry point
    workflow.set_entry_point("baseline")

    # Linear flow with conditional branches
    workflow.add_edge("baseline", "diagnose")

    # After diagnosis: if human_review needed, go to awaiting_approval directly
    workflow.add_conditional_edges(
        "diagnose",
        nodes.route_after_diagnose,
        {
            "propose": "propose",
            "awaiting_approval": "awaiting_approval",
            "end": END,
        },
    )

    workflow.add_edge("propose", "build")
    workflow.add_edge("build", "targeted_eval")
    workflow.add_edge("targeted_eval", "regression_eval")

    # Gate check: pass → approval, fail → end or retry
    workflow.add_conditional_edges(
        "regression_eval",
        nodes.route_after_regression,
        {
            "awaiting_approval": "awaiting_approval",
            "propose": "propose",  # retry with next candidate
            "end": END,
        },
    )

    workflow.add_conditional_edges(
        "awaiting_approval",
        nodes.route_after_approval,
        {
            "publish": "publish",
            "end": END,
        },
    )

    workflow.add_edge("publish", "question_evolution")
    workflow.add_edge("question_evolution", END)

    return workflow.compile()
