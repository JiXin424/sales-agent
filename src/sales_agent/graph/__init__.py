"""LangGraph-based orchestration for the Sales Agent pipeline.

Public API:
    build_chat_graph()            -- ChatPipeline as a compiled StateGraph
    build_react_agent()           -- ReAct agent with tool calling
    build_quick_session_graph()   -- Quick session state machine
    build_daily_eval_graph()      -- Daily evaluation pipeline

Checkpoints:
    get_checkpointer()            -- AsyncPostgresSaver for production
    get_checkpointer_sync()       -- InMemorySaver for tests

Func API:
    batch_score_conversations     -- @entrypoint with parallel @task scoring
"""

from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer, get_checkpointer_sync
from sales_agent.graph.react_agent import build_react_agent
from sales_agent.graph.coach.quick_session_graph import build_quick_session_graph
from sales_agent.graph.coach.daily_eval_graph import build_daily_eval_graph
from sales_agent.graph.func_tasks import batch_score_conversations

__all__ = [
    "build_chat_graph",
    "build_react_agent",
    "build_quick_session_graph",
    "build_daily_eval_graph",
    "batch_score_conversations",
    "get_checkpointer",
    "get_checkpointer_sync",
]
