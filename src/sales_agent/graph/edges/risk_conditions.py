"""Risk-based conditional edge functions."""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def check_risk_result(state: ChatGraphState) -> str:
    """Route based on risk check outcome.

    Returns:
        "pass" — answer is safe
        "block" — answer blocked, regenerate
        "rewrite" — answer needs rewrite, regenerate
        "max_retries" — no more retries, proceed anyway
    """
    action = state.get("risk_action", "allow")
    retry_count = state.get("retry_count", 0)

    if action in ("pass", "allow", "warn"):
        return "pass"
    if action == "block" and retry_count < 3:
        return "block"
    if action == "rewrite" and retry_count < 3:
        return "rewrite"
    return "max_retries"
