"""LLM generation node.

Calls :func:`execute_agent` via the chat model from Runtime.context.
Preserves the PromptRegistry 3-tier resolution for custom prompts.

P1: Emits custom stream events via ``runtime.stream_writer`` for
     progress tracking (retrieval done, generating, done).
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

    If ``skip_generation`` is set (e.g. by the evidence gate when required
    knowledge is missing), this node is a no-op that passes through.

    Requires ``runtime.context["chat_model"]`` (ChatModel instance).

    P1: Uses ``runtime.stream_writer`` to emit custom progress events
    that can be consumed via ``stream_mode="custom"``.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing ``chat_model`` and ``db``.

    Returns:
        Dict with ``answer_dict``, ``raw_response``, and ``usage``.
    """
    writer = runtime.stream_writer

    # ── Ontology subgraph already pre-computed the answer ──
    # P2: Also cover Send fan-out case where ontology result and RAG
    #     results race — if answer_dict already has content, skip.
    existing_answer = state.get("answer_dict")
    if state.get("skip_generation") or (
        existing_answer and existing_answer.get("summary")
    ):
        writer({
            "phase": "generation_skipped",
            "reason": "skip_generation_set" if state.get("skip_generation") else "fan_out_answer_present",
        })
        return {}

    chat_model = runtime.context.get("chat_model")
    task_type = state.get("task_type", "general_sales_coaching")

    # P1: Emit custom progress event
    writer({"phase": "generation_started", "task_type": task_type})

    # ── Fallback when no model is available (unit tests) ──
    if chat_model is None:
        writer({"phase": "generation_fallback", "reason": "no_model"})
        return {
            "answer_dict": {
                "summary": f"No model available for task: {task_type}",
                "sections": [],
                "sources": state.get("sources", []),
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

    # P1: Custom stream — generation in progress
    writer({"phase": "generation_executing", "task_type": task_type})

    start_time = time.time()
    ontology_context = state.get("ontology_context_text", "")

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
        ontology_context=ontology_context,
    )
    answer_dict["sources"] = state.get("sources", [])  # 透传来源供钉钉文末引用
    latency_ms = int((time.time() - start_time) * 1000)
    logger.info("Graph generation completed in %d ms for task %s", latency_ms, task_type)

    usage = getattr(chat_model, "last_usage", {}) or {}

    # P1: Custom stream — generation complete
    writer({
        "phase": "generation_complete",
        "task_type": task_type,
        "latency_ms": latency_ms,
        "usage": usage,
    })

    return {
        "answer_dict": answer_dict,
        "raw_response": json.dumps(answer_dict, ensure_ascii=False),
        "usage": usage,
    }
