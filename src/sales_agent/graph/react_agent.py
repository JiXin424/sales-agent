"""ReAct agent using LangGraph's create_react_agent.

This is the foundation for autonomous tool-calling agents.
The agent decides which tools to call, in what order, and when to stop --
without a fixed workflow.
"""

from __future__ import annotations

from langgraph.prebuilt import create_react_agent
from langgraph.graph.state import CompiledStateGraph

from sales_agent.graph.tools import ALL_TOOLS


def build_react_agent(
    chat_model,
    checkpointer=None,
) -> CompiledStateGraph:
    """Build a ReAct-style agent with tool-calling capability.

    The agent autonomously:
    1. Reasons about the user's request
    2. Decides whether to call a tool or respond
    3. Loops until it has enough information to answer

    Args:
        chat_model: The chat model (must support tool calling).
        checkpointer: Optional checkpointer for state persistence.

    Returns:
        A compiled StateGraph ready for invoke/stream.
    """
    agent = create_react_agent(
        model=chat_model,
        tools=ALL_TOOLS,
        checkpointer=checkpointer,
    )
    return agent
