"""Shared state definition for the ChatPipeline graph."""

from __future__ import annotations

from typing import Annotated, Any
from operator import add
from typing_extensions import TypedDict


class ChatGraphState(TypedDict, total=False):
    """Shared state flowing through all ChatPipeline graph nodes.

    `total=False` means all keys are optional — each node only needs to
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
    retrieval_result: Any
    sources: list[dict]
    skip_generation: bool

    # === Coach Guidance ===
    coach_guidance_text: str

    # === Generation ===
    answer_dict: dict[str, Any]
    raw_response: str

    # === Risk ===
    input_risk_level: str                  # "none" | "low" | "medium" | "high"
    risk_result: dict[str, Any]
    risk_action: str                       # "allow" | "warn" | "rewrite" | "block"

    # === Control ===
    retry_count: int
    error: str | None

    # === Streaming tokens ===
    stream_tokens: Annotated[list[str], add]

    # === Output ===
    final_answer: dict[str, Any]
    final_sources: list[dict]
    run_id: str | None
    usage: dict[str, int]
