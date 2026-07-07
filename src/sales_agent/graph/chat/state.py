"""Shared state definition for the ChatPipeline graph.

Includes support for:
- ``IsLastStep`` managed value (LangGraph built-in)
- ``Overwrite`` support via ``operator.add`` on list-typed fields
- HITL (human-in-the-loop) fields for interrupt/Command

Reducer 约束（重要）：任何会被 ``Send`` 并行 fan-out 写入的字段，必须用
``Annotated[T, reducer]`` 声明合并策略，否则 LangGraph 合并并行结果时会抛
``InvalidUpdateError``（见 commit eb889df）。当前会被并行双路（ontology + rag）
写入的字段：``sources``、``retrieval_info``、``retrieval_result``、
``ontology_context_text``、``skip_generation``、``stream_tokens``。
新增任何被并行写的字段时，务必同步加 reducer。
"""

from __future__ import annotations

from typing import Annotated, Any
from operator import add


# -- Reducers for parallel Send fan-out --
def _reduce_or(a: bool | None, b: bool | None) -> bool | None:
    """Reducer for bool fields: True if either parallel result is True.
    Handles initial `None` gracefully (returns `b` if `a` is None)."""
    if a is None:
        return b
    if b is None:
        return a
    return a or b


def _reduce_merge_dict(a: dict | None, b: dict | None) -> dict | None:
    """Reducer for dict fields: shallow merge from parallel results."""
    if a is None and b is None:
        return None
    merged = dict(a) if a else {}
    if b:
        merged.update(b)
    return merged


def _reduce_coalesce(a: Any, b: Any) -> Any:
    """Reducer for single-value fields under parallel Send fan-out: keep the
    first non-empty value. ``None`` / empty string / empty container are
    treated as absent so the other parallel result wins.

    Used for ``ontology_context_text`` (ontology 路权威，rag 路 web_fallback
    也写) and ``retrieval_result`` (rag 路写) — 两路同时写时避免
    ``InvalidUpdateError``，且任一路有值都不丢。
    """
    if not a:
        return b
    if not b:
        return a
    return a
from typing_extensions import TypedDict

from langgraph.managed.is_last_step import IsLastStep


class ChatGraphState(TypedDict, total=False):
    """Shared state flowing through all ChatPipeline graph nodes.

    ``total=False`` means all keys are optional — each node only needs to
    set the fields it contributes.
    """

    # === Input (set by caller) ===
    tenant_id: str
    user_id: str
    message: str
    conversation_id: str
    channel: str                           # "local" | "dingtalk"
    agent_id: str | None
    model_override: str | None

    # === Topic / Context Resolution (set by Online Graph) ===
    topic_id: str | None
    knowledge_policy: str | None           # "none" | "optional" | "required"
    precomputed_route: bool | None         # skip routing_node when True

    # === Tenant + Model Resolution ===
    tenant_info: dict[str, Any]
    # model_provider is NOT in State — passed via Runtime.context

    # === Context Loading ===
    history_messages: list[dict[str, str]]

    # === Task Routing ===
    task_type: str
    route_confidence: float
    needs_retrieval: bool
    needs_clarification: bool

    # === Prompt Resolution ===
    prompt_text: str | None
    system_prompt_text: str | None

    # === Path Decision ===
    path: str                              # "fast" | "standard" | "slow"
    path_reason: str

    # === Retrieval ===
    retrieval_path: str                    # "ontology" | "rag" | "skip"
    retrieval_info: Annotated[dict[str, Any], _reduce_merge_dict]
    retrieval_result: Annotated[Any, _reduce_coalesce]
    sources: Annotated[list[dict], add]  # P2: reducer merges parallel Send results
    ontology_context_text: Annotated[str, _reduce_coalesce]  # ontology + rag(web) 并行写
    skip_generation: Annotated[bool, _reduce_or]

    # === Coach Guidance ===
    coach_guidance_text: str

    # === Generation ===
    answer_dict: dict[str, Any]
    raw_response: str

    # === Risk ===
    input_risk_level: str                  # "none" | "low" | "medium" | "high"
    risk_result: dict[str, Any]
    risk_action: str                       # "allow" | "warn" | "rewrite" | "block" | "human_review"
    human_review_approved: bool            # HITL: set by Command(resume=...) on resume

    # === Control ===
    retry_count: int
    error: str | None

    # === Managed Values (LangGraph built-in) ===
    is_last_step: IsLastStep  # automatically set by framework

    # === Streaming tokens ===
    stream_tokens: Annotated[list[str], add]

    # === Output ===
    final_answer: dict[str, Any]
    final_sources: list[dict]
    run_id: str | None
    usage: dict[str, int]
