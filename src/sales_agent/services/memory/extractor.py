from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from sales_agent.prompts.memory_extractor_prompt import MEMORY_EXTRACTOR_PROMPT
from sales_agent.services.memory.contracts import MemoryCandidate
from sales_agent.services.memory.policy import classify_sensitivity, classify_stability
from sales_agent.services.structured_router_output import parse_model_json

logger = logging.getLogger(__name__)


class MemoryExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


async def extract_memory_candidates(
    *,
    user_message: str,
    topic_summary: str,
    verified_tool_facts: list[dict],
    chat_model,
) -> list[MemoryCandidate]:
    user_content = (
        f"用户当前消息：{user_message}\n"
        f"当前 Topic 摘要：{topic_summary}\n"
        f"已验证工具事实：{verified_tool_facts}\n"
    )
    messages = [
        {"role": "system", "content": MEMORY_EXTRACTOR_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = await chat_model.generate(messages=messages, temperature=0.0, max_tokens=700)
        parsed = parse_model_json(raw, MemoryExtractionResult)
    except Exception as exc:
        logger.warning("memory extractor parse failure: %s", exc)
        return []

    safe: list[MemoryCandidate] = []
    for candidate in parsed.candidates:
        sensitivity = classify_sensitivity(candidate.evidence_text)
        stability = classify_stability(candidate.evidence_text)
        if sensitivity == "prohibited":
            continue
        if stability != "stable":
            continue
        safe.append(
            candidate.model_copy(
                update={
                    "source_kind": "inferred_user",
                    "sensitivity": sensitivity,
                    "stability": stability,
                    "confidence_band": "candidate",
                }
            )
        )
    return safe
