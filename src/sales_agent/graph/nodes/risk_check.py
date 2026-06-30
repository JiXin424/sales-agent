"""Risk checking node.

Calls the existing RiskChecker service for input pre-check and output post-check.
"""

from __future__ import annotations

import json
import logging
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.risk_checker import RiskChecker

logger = logging.getLogger(__name__)


def risk_check_node(state: ChatGraphState) -> dict:
    """Run risk checks against the generated answer.

    Checks: input pre-check (rule-based on user message) and output
    post-check (rule-based on generated answer). LLM risk check is
    deferred to a conditional edge in a later phase.

    Args:
        state: Current graph state with ``answer_dict`` populated.

    Returns:
        Dict with ``risk_action``, ``risk_result``, and ``input_risk_level``.
    """
    message = state.get("message", "")
    answer_dict = state.get("answer_dict", {})
    tenant_id = state.get("tenant_id", "")
    sources = state.get("sources", [])

    checker = RiskChecker()
    answer_text = json.dumps(answer_dict, ensure_ascii=False)

    # Full check: input + source + output
    result = checker.full_check(
        message=message,
        sources=sources,
        tenant_id=tenant_id,
        answer_text=answer_text,
    )

    # If blocked, replace answer with safety notice
    new_answer = dict(answer_dict)
    if result.action == "block":
        new_answer = {
            "summary": result.notice or "该请求涉及高风险承诺，已改为安全建议",
            "sections": [
                {"title": "安全提示", "content": result.notice},
                {"title": "建议", "content": "请使用合规的销售表达，不要对外做出未确认的承诺。"},
            ],
        }

    return {
        "risk_action": result.action,
        "risk_result": result.to_dict(),
        "input_risk_level": result.level,
        "answer_dict": new_answer,
    }
