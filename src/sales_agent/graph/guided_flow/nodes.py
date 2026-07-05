"""Graph nodes for the one-turn Guided Flow subgraph.

Nodes are pure state-mutators — the start / advance callbacks are loaded
from the registry (``definitions``) on every call, never stored in state.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from sales_agent.graph.guided_flow.definitions import get_flow_definition
from sales_agent.graph.guided_flow.state import GuidedFlowState
from sales_agent.graph.guided_flow.types import FlowServices

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _unpack_context(config: RunnableConfig) -> tuple[Any, Any]:
    """Extract ``(db, chat_model)`` from the langgraph run context."""
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime else None
    if ctx is not None:
        return ctx.get("db"), ctx.get("chat_model")
    return None, None


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

def start_flow_node(state: GuidedFlowState, config: RunnableConfig) -> dict[str, Any]:
    """Handle ``flow_action == "start"``.

    1. Resolve the flow ID from ``state["requested_flow"]``.
    2. Look up its ``FlowDefinition`` in the registry.
    3. Call the synchronous *start* function.
    4. Write ``active_flow``, ``flow_stage``, ``flow_payload`` and an
       ``answer_dict`` containing the first question.

    The payload is replaced with whatever the start handler returns
    (generally an empty dict).
    """
    flow_id = state.get("requested_flow")
    if not flow_id:
        return {
            "response_kind": "flow_retry",
            "answer_dict": {"summary": "无法识别引导流程。", "sections": []},
        }

    definition = get_flow_definition(flow_id)
    result = definition.start()

    return {
        "active_flow": flow_id,
        "flow_stage": result.stage,
        "flow_payload": result.payload,
        "answer_dict": {"summary": result.reply, "sections": []},
        "response_kind": "flow_question",
    }


async def advance_flow_node(
    state: GuidedFlowState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Handle ``flow_action == "advance"``.

    1. Resolve ``active_flow`` from state.
    2. Call the async *advance* function with ``FlowServices`` built from
       the run context.
    3. If the flow is **completed**: clear ``active_flow``, ``flow_stage``,
       ``flow_payload`` and set ``completed_flow``.
    4. If **not completed**: update ``flow_stage`` / ``flow_payload``.

    On any handler exception the existing flow state is preserved and a
    ``response_kind="flow_retry"`` is returned so the caller can re-prompt.
    """
    flow_id = state.get("active_flow")
    if not flow_id:
        return {
            "answer_dict": {"summary": "没有进行中的引导流程。", "sections": []},
            "response_kind": "error",
        }

    definition = get_flow_definition(flow_id)
    stage = state.get("flow_stage") or ""
    payload = state.get("flow_payload") or {}
    text = state.get("message") or ""

    db, chat_model = _unpack_context(config)
    services = FlowServices(
        db=db,
        chat_model=chat_model,
        tenant_id=state.get("tenant_id", ""),
        agent_id=state.get("agent_id", ""),
    )

    try:
        result = await definition.advance(stage, payload, text, services)
    except Exception:
        logger.exception("advance_flow_node failed for flow=%s stage=%s", flow_id, stage)
        return {
            "response_kind": "flow_retry",
            "answer_dict": {
                "summary": "刚才处理失败了，请重新回答当前问题。",
                "sections": [],
            },
        }

    if result.completed:
        return {
            "active_flow": None,
            "flow_stage": None,
            "flow_payload": None,
            "completed_flow": flow_id,
            "answer_dict": {"summary": result.reply, "sections": []},
            "response_kind": "flow_completed",
        }

    return {
        "flow_stage": result.stage,
        "flow_payload": result.payload,
        "answer_dict": {"summary": result.reply, "sections": []},
        "response_kind": "flow_question",
    }


def cancel_flow_node(state: GuidedFlowState, config: RunnableConfig) -> dict[str, Any]:
    """Handle ``flow_action == "cancel"``.

    Clears all flow-tracking fields and returns a plain-text goodbye
    message.
    """
    return {
        "active_flow": None,
        "flow_stage": None,
        "flow_payload": None,
        "completed_flow": None,
        "response_kind": "flow_cancelled",
        "answer_dict": {
            "summary": "已退出当前引导流程，你可以正常提问了。",
            "sections": [],
        },
    }


__all__ = [
    "advance_flow_node",
    "cancel_flow_node",
    "start_flow_node",
]
