"""Evidence Routing Node — intent classification and knowledge policy.

Integrates the ``route_intent_evidence`` service into the Online
Conversation Graph.

Only runs when the context has been resolved (``context_status == "resolved"``).
For clarify paths this node is a no-op.

Maps the validated :class:`EvidenceDecision` onto graph state fields:
*task_type*, *route_confidence*, *knowledge_policy*, *knowledge_scope*,
*retrieval_query*, *needs_retrieval*, and *route_trace*.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from sales_agent.services.evidence_router import route_intent_evidence

logger = logging.getLogger(__name__)


def _unpack_context(config: RunnableConfig) -> dict[str, Any] | None:
    """Extract the runtime context dict from the LangGraph config."""
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime else None
    return ctx


async def evidence_routing_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Route intent and evidence policy for a context-resolved user message.

    This node is a **no-op** (returns ``{}``) when:

    - ``context_status`` is not ``"resolved"`` (e.g. clarify path).
    - Neither ``evidence_router_override`` nor *chat_model* is available.

    Args:
        state: Current graph state — must contain ``standalone_query``
            and ``context_status``.
        config: LangGraph ``RunnableConfig`` carrying runtime context.

    Returns:
        A partial state dict with evidence-routing fields, or ``{}``
        when skipped.
    """
    # Only run for resolved context
    if state.get("context_status") != "resolved":
        return {}

    ctx = _unpack_context(config)
    chat_model = ctx.get("chat_model") if ctx else None
    db = ctx.get("db") if ctx else None
    tenant_id = state.get("tenant_id") or None
    agent_id = state.get("agent_id") or None
    evidence_router_fn = (
        ctx.get("evidence_router_override") if ctx else None
    ) or route_intent_evidence

    standalone_query = state.get("standalone_query", "")

    # No model and no override — passthrough defaults
    if not standalone_query and not ctx:
        return {}

    # Call the evidence router
    decision = await evidence_router_fn(
        standalone_query=standalone_query,
        chat_model=chat_model,
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )

    # Map validated decision to state fields
    needs_retrieval = decision.knowledge_policy in ("required", "optional")

    return {
        "task_type": decision.intent,
        "route_confidence": decision.confidence,
        "knowledge_policy": decision.knowledge_policy,
        "knowledge_scope": decision.knowledge_scope,
        "retrieval_query": decision.retrieval_query,
        "needs_retrieval": needs_retrieval,
        "route_trace": decision.reason_code,
        "response_kind": "chat",
    }
