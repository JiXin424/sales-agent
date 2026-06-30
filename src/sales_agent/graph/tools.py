"""Tool definitions for the ReAct agent.

LangGraph's ToolNode wraps these as callable nodes in the agent graph.
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_knowledge_base(query: str) -> str:
    """Search the enterprise knowledge base for product/company information.

    Args:
        query: The search query string.

    Returns:
        Relevant knowledge base content.
    """
    # Stub -- real implementation calls the existing HybridRetriever
    return f"[Knowledge base result for: {query}]"


@tool
def check_customer_history(customer_name: str) -> str:
    """Retrieve past interaction history with a specific customer.

    Args:
        customer_name: The customer's name or identifier.

    Returns:
        Summary of past interactions.
    """
    return f"[Customer history for: {customer_name}]"


@tool
def get_sales_script_template(scenario: str) -> str:
    """Get a sales script template for a given scenario.

    Args:
        scenario: The scenario type (e.g. "cold_call", "follow_up", "objection").

    Returns:
        A structured script template.
    """
    return f"[Script template for: {scenario}]"


# All tools available to the agent
ALL_TOOLS = [
    search_knowledge_base,
    check_customer_history,
    get_sales_script_template,
]
