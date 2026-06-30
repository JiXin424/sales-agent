"""Daily Evaluation pipeline as a LangGraph subgraph.

Converts the implicit multi-stage flow in ``coach/daily_evaluator.py``
into an explicit DAG: aggregate --> score --> validate --> apply --> progress --> reward.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END


class DailyEvalState(TypedDict, total=False):
    """State for the daily evaluation subgraph."""
    tenant_id: str
    agent_id: str
    user_id: str
    eval_date: str
    conversations: list[dict]
    scores: dict[str, int]
    previous_scores: dict[str, int]
    validation_passed: bool
    milestones_unlocked: list[str]
    rewards_granted: list[dict]
    error: str | None


def aggregate_conversations(state: DailyEvalState) -> dict:
    """Aggregate today's conversations for this user."""
    return {"conversations": []}  # Stub -- full impl reads from DB


def llm_scoring_node(state: DailyEvalState) -> dict:
    """Call LLM to score the day's conversations across 6 dimensions."""
    # Stub -- full impl calls coach_daily_evaluation prompt
    return {
        "scores": {
            "customer_identification": 50,
            "needs_discovery": 50,
            "value_delivery": 50,
            "trust_building": 50,
            "deal_advancement": 50,
            "review_reflection": 50,
        },
        "validation_passed": True,
    }


def validate_json_node(state: DailyEvalState) -> dict:
    """Validate LLM JSON output against schema."""
    scores = state.get("scores", {})
    required_dims = [
        "customer_identification", "needs_discovery", "value_delivery",
        "trust_building", "deal_advancement", "review_reflection",
    ]
    for dim in required_dims:
        if dim not in scores or not (0 <= scores[dim] <= 100):
            return {"validation_passed": False, "error": f"Invalid score for {dim}"}
    return {"validation_passed": True}


def apply_scores_node(state: DailyEvalState) -> dict:
    """Apply score deltas to persistent storage."""
    return {}


def check_milestones_node(state: DailyEvalState) -> dict:
    """Check and unlock milestones."""
    return {"milestones_unlocked": []}


def grant_rewards_node(state: DailyEvalState) -> dict:
    """Grant probabilistic rewards."""
    return {"rewards_granted": []}


def build_daily_eval_graph() -> StateGraph:
    """Build the daily evaluation subgraph.

    Graph structure::

        START --> aggregate --> llm_score --> validate_json
                            ^                  |
                            +--(retry)---------+  (validation_passed==False)
                                    |
                                    v (passed)
                              apply_scores --> check_milestones --> grant_rewards --> END
    """
    builder = StateGraph(DailyEvalState)

    builder.add_node("aggregate", aggregate_conversations)
    builder.add_node("llm_score", llm_scoring_node)
    builder.add_node("validate_json", validate_json_node)
    builder.add_node("apply_scores", apply_scores_node)
    builder.add_node("check_milestones", check_milestones_node)
    builder.add_node("grant_rewards", grant_rewards_node)

    builder.add_edge(START, "aggregate")
    builder.add_edge("aggregate", "llm_score")
    builder.add_edge("llm_score", "validate_json")
    builder.add_conditional_edges(
        "validate_json",
        lambda s: "apply" if s.get("validation_passed") else "retry",
        {"apply": "apply_scores", "retry": "llm_score"},
    )
    builder.add_edge("apply_scores", "check_milestones")
    builder.add_edge("check_milestones", "grant_rewards")
    builder.add_edge("grant_rewards", END)

    return builder
