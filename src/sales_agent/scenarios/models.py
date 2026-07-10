"""Pydantic / dataclass models for the scenario coach."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class AnswerSection:
    """One rendered section of a preset answer: {title, content}."""

    title: str
    content: str


@dataclass
class ScenarioQuestion:
    """A single representative question (Q01..Q11) and its preset answer."""

    id: str
    text: str
    tag: str
    answer_summary: str
    answer_sections: list[AnswerSection] = field(default_factory=list)


@dataclass
class Scenario:
    """A scenario group (S1..S6) containing 1-3 questions."""

    id: str
    name: str
    subtitle: str
    questions: list[ScenarioQuestion] = field(default_factory=list)


class ScenarioMatchDecision(BaseModel):
    """LLM decision for scenario matching.

    matched_question_id is None when no preset question matches (or when
    confidence is below threshold / parse failed — the matcher normalizes
    all non-hit cases to None before returning).
    """

    matched_question_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason_code: str = "unknown"
