"""Risk checking node with HITL (human-in-the-loop) support.

Calls the existing RiskChecker service for input pre-check and output post-check.
When risk_action == "human_review", uses LangGraph ``interrupt()`` to pause
execution and wait for human approval via ``Command(resume=...)``.

P0: For now, high-risk requests pass through automatically
    (``human_review_approved`` defaults to True in auto-approval mode).
"""

from __future__ import annotations

import json
import logging
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.risk_checker import RiskChecker
from langgraph.types import interrupt

logger = logging.getLogger(__name__)

# Auto-approval flag — set to False to enable real HITL review
_AUTO_APPROVE_HIGH_RISK = True


def _build_risk_interrupt_message(state: ChatGraphState) -> dict:
    """Build the interrupt payload for human review of a risky answer."""
    return {
        "type": "human_review",
        "question": "高风险回复需要人工审批",
        "message": state.get("message", ""),
        "risk_action": state.get("risk_action", "unknown"),
        "risk_result": state.get("risk_result", {}),
        "raw_response": state.get("raw_response", ""),
        "current_answer": state.get("answer_dict", {}),
        "tenant_id": state.get("tenant_id", ""),
    }


def risk_check_node(state: ChatGraphState) -> dict:
    """Run risk checks against the generated answer.

    Checks: input pre-check (rule-based on user message) and output
    post-check (rule-based on generated answer). LLM risk check is
    deferred to a conditional edge in a later phase.

    P0: High-risk results marked ``human_review`` trigger an ``interrupt()``
    for human approval. The graph pauses until a ``Command(resume=...)``
    is sent. For now, auto-approve is enabled — all high-risk passes.

    Args:
        state: Current graph state with ``answer_dict`` populated.

    Returns:
        Dict with ``risk_action``, ``risk_result``, ``input_risk_level``.
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

    new_answer = dict(answer_dict)
    risk_action = result.action

    # ── P0: HITL — human review path (auto-approve for now) ─────────
    if risk_action in ("block",) and _AUTO_APPROVE_HIGH_RISK:
        # Auto-approve: treat block as warn (pass through with warning)
        risk_action = "warn"
        logger.info(
            "High-risk (%s / %s) auto-approved (HITL bypassed)",
            result.level, result.action,
        )
    elif risk_action == "block":
        # Real HITL mode: interrupt and wait for human review
        review_payload = _build_risk_interrupt_message(state)
        human_decision = interrupt(review_payload)

        if isinstance(human_decision, dict) and human_decision.get("action") == "approve":
            risk_action = "pass"
        elif isinstance(human_decision, dict) and human_decision.get("rewrite_hint"):
            risk_action = "rewrite"
            new_answer["_rewrite_hint"] = human_decision["rewrite_hint"]
        else:
            risk_action = "block"

    # If blocked, replace answer with safety notice
    # 注意：summary 和 sections 不要放相同内容，避免 format_text_output 输出两遍。
    if risk_action == "block":
        _notice = result.notice or "该请求涉及高风险承诺，已改为安全建议"
        new_answer = {
            "summary": _notice,
            "sections": [
                {"title": "建议", "content": "请使用合规的销售表达，不要对外做出未确认的承诺。"},
            ],
        }

    return {
        "risk_action": risk_action,
        "risk_result": result.to_dict(),
        "input_risk_level": result.level,
        "answer_dict": new_answer,
        "human_review_approved": _AUTO_APPROVE_HIGH_RISK,
    }
