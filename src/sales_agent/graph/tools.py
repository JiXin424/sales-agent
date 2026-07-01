"""Tool definitions for the ReAct agent.

LangGraph's ToolNode wraps these as callable nodes in the agent graph.

Features:
- ``InjectedState`` — auto-injects graph state into tools
- ``InjectedStore`` — auto-injects cross-session Store into tools
- ``ToolRuntime`` — full runtime context (config, state, tool_call_id)
"""

from __future__ import annotations

from typing import Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState, InjectedStore, ToolRuntime
from langgraph.store.base import BaseStore

from sales_agent.graph.state import ChatGraphState


@tool
def search_knowledge_base(
    query: str,
    state: Annotated[dict, InjectedState] = {},
    runtime: ToolRuntime = None,
) -> str:
    """Search the enterprise knowledge base for product/company information.

    Uses the tenant from graph state to scope searches.

    Args:
        query: The search query string.

    Returns:
        Relevant knowledge base content.
    """
    tenant = state.get("tenant_id", "unknown")
    tool_call_id = getattr(runtime, "tool_call_id", "unknown") if runtime else "unknown"
    return f"[Knowledge base result for tenant={tenant}: {query}] (call={tool_call_id[:8]})"


@tool
def check_customer_history(
    customer_name: str,
    state: Annotated[dict, InjectedState] = {},
    store: Annotated[BaseStore, InjectedStore] = None,
    runtime: ToolRuntime = None,
) -> str:
    """Retrieve past interaction history with a specific customer.

    Accesses the LangGraph Store for cross-session customer profiles.

    Args:
        customer_name: The customer's name or identifier.

    Returns:
        Summary of past interactions.
    """
    tenant = state.get("tenant_id", "unknown")
    task_type = state.get("task_type", "unknown")

    # Try reading from Store (cross-session memory)
    if store is not None:
        try:
            item = store.get(("customers", tenant), customer_name)
            if item and item.value:
                return (
                    f"[Customer history for {customer_name}]: {item.value}\n"
                    f"(context: task={task_type})"
                )
        except Exception:
            pass

    return f"[Customer history for: {customer_name}] (no prior data, task={task_type})"


@tool
def get_sales_script_template(
    scenario: str,
    state: Annotated[dict, InjectedState] = {},
    runtime: ToolRuntime = None,
) -> str:
    """Get a sales script template for a given scenario.

    Args:
        scenario: The scenario type (e.g. "cold_call", "follow_up", "objection").

    Returns:
        A structured script template.
    """
    tenant = state.get("tenant_id", "unknown")
    task_type = state.get("task_type", "unknown")
    return f"[Script template for {scenario} (tenant={tenant}, task={task_type})]"


# All tools available to the agent
ALL_TOOLS = [
    search_knowledge_base,
    check_customer_history,
    get_sales_script_template,
]
