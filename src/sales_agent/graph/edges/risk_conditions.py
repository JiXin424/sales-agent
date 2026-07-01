"""Risk-based conditional edge functions.

P0: Added ``human_review`` path for HITL (human-in-the-loop) approval.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def check_risk_result(state: ChatGraphState) -> str:
    """Route based on risk check outcome.

    Returns:
        "pass" — answer is safe, proceed to log
        "block" — answer blocked, regenerate with safety notice
        "rewrite" — answer needs rewrite, regenerate with rewrite hint
        "human_review" — P0: HITL interrupt was triggered, proceed to log
        "max_retries" — no more retries, proceed anyway
    """
    action = state.get("risk_action", "allow")
    retry_count = state.get("retry_count", 0)

    if action in ("pass", "allow", "warn"):
        return "pass"

    # P0: HITL — human review already handled inside risk_check_node
    if action == "human_review":
        return "human_review"

    if action == "block" and retry_count < 3:
        return "block"
    if action == "rewrite" and retry_count < 3:
        return "rewrite"
    return "max_retries"
