"""LLM-based scenario matcher (mirrors services/evidence_router.py)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sales_agent.llm.call_params import get_call_params
from sales_agent.scenarios.loader import ScenarioRegistry, get_scenario_registry
from sales_agent.scenarios.models import ScenarioMatchDecision
from sales_agent.scenarios.prompt import SCENARIO_MATCHER_PROMPT
from sales_agent.services.structured_router_output import parse_model_json

logger = logging.getLogger(__name__)


def _no_match(reason_code: str, confidence: float = 0.0) -> ScenarioMatchDecision:
    return ScenarioMatchDecision(
        matched_question_id=None, confidence=confidence, reason_code=reason_code
    )


async def match_scenario(
    message: str,
    *,
    chat_model: Any,
    confidence_threshold: float,
    registry: ScenarioRegistry | None = None,
) -> ScenarioMatchDecision:
    """Match a user message against preset scenario questions.

    Returns a ScenarioMatchDecision with matched_question_id set only when a
    preset question is matched at or above confidence_threshold. Fail-open:
    any error → no match (lesson #34).
    """
    if registry is None:
        registry = get_scenario_registry()
    if not registry.is_available():
        return _no_match("registry_unavailable")
    if not message or not message.strip():
        return _no_match("empty_message")

    questions = registry.list_questions()
    system_prompt = SCENARIO_MATCHER_PROMPT.format(
        questions_json=json.dumps(questions, ensure_ascii=False, indent=2)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"用户问题：{message}"},
    ]

    for attempt in range(2):
        try:
            p = get_call_params("scenario_matcher")
            response = await chat_model.generate(
                messages=messages,
                temperature=p.temperature,
                max_tokens=p.max_tokens,
            )
            decision = parse_model_json(response, ScenarioMatchDecision)

            # Validate the matched id is a real preset question.
            if decision.matched_question_id is not None and not registry.has_question(
                decision.matched_question_id
            ):
                logger.warning(
                    "scenario_matcher: unknown question id '%s'", decision.matched_question_id
                )
                decision.matched_question_id = None
                decision.reason_code = "unknown_question_id"

            # Apply the confidence threshold.
            if decision.matched_question_id is not None and decision.confidence < confidence_threshold:
                logger.debug(
                    "scenario_matcher: match %s below threshold %.2f (conf=%.2f)",
                    decision.matched_question_id,
                    confidence_threshold,
                    decision.confidence,
                )
                decision.matched_question_id = None
                decision.reason_code = "below_threshold"

            return decision

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "scenario_matcher parse failure (attempt %d/2): %s", attempt + 1, exc
            )
            if attempt == 0:
                messages.append(
                    {
                        "role": "user",
                        "content": "输出格式不符合 JSON 规范，请仅输出一个合法的 JSON 对象，不要包含其他任何内容。",
                    }
                )
                continue
            return _no_match("parse_failure")

        except Exception as exc:
            # LLM / network failure: fail-open immediately (lesson #34).
            logger.warning("scenario_matcher LLM failure: %s", exc)
            return _no_match("llm_failure")

    return _no_match("parse_failure")
