"""LLM generation node.

Calls :func:`execute_agent` via the chat model from Runtime.context.
Preserves the PromptRegistry 3-tier resolution for custom prompts.
"""

from __future__ import annotations

import json
import logging
import time

from langgraph.runtime import Runtime

from sales_agent.graph.state import ChatGraphState
from sales_agent.services.agent_executor import execute_agent
from sales_agent.services.prompt_resolver_helper import resolve_execution_prompts

logger = logging.getLogger(__name__)


async def generate_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Execute the LLM agent to generate a structured answer.

    If ``skip_generation`` is set (e.g. by the ontology subgraph), this
    node is a no-op that passes through.

    Requires ``runtime.context["chat_model"]`` (ChatModel instance).

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing ``chat_model`` and ``db``.

    Returns:
        Dict with ``answer_dict``, ``raw_response``, and ``usage``.
    """
    # ── Ontology subgraph already pre-computed the answer ──
    if state.get("skip_generation"):
        return {}

    chat_model = runtime.context.get("chat_model")
    task_type = state.get("task_type", "general_sales_coaching")

    # ── Fallback when no model is available (unit tests) ──
    if chat_model is None:
        return {
            "answer_dict": {
                "summary": f"No model available for task: {task_type}",
                "sections": [],
            },
            "raw_response": "{}",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    db = runtime.context.get("db")
    agent_id = state.get("agent_id")
    tenant_id = state["tenant_id"]

    # ── PromptRegistry 3-tier resolution ──
    # This is how the 22 custom prompts are loaded — do NOT skip this step.
    prompt_text: str | None = state.get("prompt_text")
    system_prompt_text: str | None = state.get("system_prompt_text")

    if db is not None:
        try:
            resolved_prompt, resolved_system = await resolve_execution_prompts(
                db, agent_id, tenant_id, task_type,
            )
            if resolved_prompt:
                prompt_text = resolved_prompt
            if resolved_system:
                system_prompt_text = resolved_system
        except Exception:
            logger.warning("Prompt resolution failed, using state-level prompts", exc_info=True)

    message = state["message"]
    history_messages = state.get("history_messages", [])

    start_time = time.time()
    answer_dict = await execute_agent(
        chat_model=chat_model,
        task_type=task_type,
        message=message,
        context={},
        retrieval_result=state.get("retrieval_result"),
        history_messages=history_messages,
        tenant_style=state.get("tenant_info", {}).get("config", {}),
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )
    latency_ms = int((time.time() - start_time) * 1000)
    logger.info("Graph generation completed in %d ms for task %s", latency_ms, task_type)

    usage = getattr(chat_model, "last_usage", {}) or {}

    return {
        "answer_dict": answer_dict,
        "raw_response": json.dumps(answer_dict, ensure_ascii=False),
        "usage": usage,
    }
