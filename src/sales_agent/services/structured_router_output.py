"""Pydantic contracts and JSON parsing for bounded router output.

Defines the validated structured output schemas that downstream services
(Context Resolver, Evidence Router, Clarification Resolver) use for
LLM-guided routing decisions.
"""

from __future__ import annotations

import json
import re
from typing import Literal, Optional, TypeVar

from json_repair import repair_json
from pydantic import BaseModel, Field, model_validator

T = TypeVar("T", bound=BaseModel)


def parse_model_json(raw: str, schema: type[T]) -> T:
    """Parse a JSON string (possibly fenced in ```json … ```) into the given model.

    Falls back to *json-repair* if standard ``json.loads`` fails.

    Parameters
    ----------
    raw : str
        Raw LLM output containing JSON.
    schema : type[T]
        Pydantic model class to instantiate.

    Returns
    -------
    T
        An instance of *schema* validated against the parsed JSON.
    """
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = repair_json(text, return_objects=True)
    if not isinstance(payload, dict):
        raise ValueError("router output must be a JSON object")
    return schema.model_validate(payload)


class ContextDecision(BaseModel):
    """Decision made by the Context Resolver router.

    Attributes
    ----------
    turn_relation :
        How this turn relates to the conversation context.
    standalone_query :
        The user query rephrased as a self-contained question.
    retained_entities :
        Entities retained from the conversation context (default empty).
    retracted_goals :
        Goals that have been retracted or superseded (default empty).
    missing_references :
        References that are expected but missing from the context (default empty).
    confidence :
        Confidence in the routing decision (0-1).
    reason_code :
        Machine-readable reason for the decision.
    """

    turn_relation: Literal["continue", "revise", "switch", "new", "ambiguous"]
    standalone_query: str
    retained_entities: list[str] = Field(default_factory=list)
    retracted_goals: list[str] = Field(default_factory=list)
    missing_references: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reason_code: str


class EvidenceDecision(BaseModel):
    """Decision made by the Evidence Router.

    Attributes
    ----------
    intent :
        The detected user intent label.
    response_mode :
        Whether to respond directly or retrieve evidence first.
    knowledge_policy :
        Whether knowledge retrieval is required, optional, or not needed.
    knowledge_scope :
        Which knowledge scopes to search (default empty).
    retrieval_query :
        The query used for retrieval (required when policy is "required").
    confidence :
        Confidence in the routing decision (0-1).
    reason_code :
        Machine-readable reason for the decision.
    """

    intent: str
    response_mode: Literal["direct", "retrieve"]
    knowledge_policy: Literal["none", "optional", "required"]
    knowledge_scope: list[str] = Field(default_factory=list)
    retrieval_query: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    reason_code: str

    @model_validator(mode="after")
    def _require_retrieval_query_when_required(self) -> EvidenceDecision:
        if self.knowledge_policy == "required" and self.retrieval_query is None:
            raise ValueError(
                "retrieval_query must be provided when knowledge_policy is 'required'"
            )
        return self


class ClarificationDecision(BaseModel):
    """Decision made by the Clarification Resolver.

    Attributes
    ----------
    resolution :
        How to resolve the clarification need.
    supplemental_message :
        Optional message or question to ask the user for clarification.
    replacement_text :
        Optional replacement text when resolution is "replace".
    confidence :
        Confidence in the routing decision (0-1).
    """

    resolution: Literal["continue", "new", "replace", "cancel"]
    supplemental_message: str | None = None
    replacement_text: str | None = None
    confidence: float = Field(ge=0, le=1)
