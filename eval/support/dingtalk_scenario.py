"""DingTalk multi-turn memory scenario schemas and runner.

Schema
------
- ``ShortTermScenario`` — one multi-turn scenario (2-6 turns).
- ``ScenarioTurn`` — a single turn in a scenario (input, timing, expectations).
- ``ExpectedTurn`` — what the turn should produce.
- ``ObservedScenarioTurn`` — what the runner observed.

The runner calls ``handle_dingtalk_event`` for each turn and returns
``ObservedScenarioTurn`` structures for evaluation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ExpectedTurn(BaseModel):
    """Expected outcome for a single scenario turn."""

    response_kind: str | None = None
    turn_relation: str | None = None
    standalone_query_contains: list[str] = Field(default_factory=list)
    retained_entities: list[str] = Field(default_factory=list)
    topic_transition: Literal["same", "new", "restored", "none"] | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_count: int = 1


class ScenarioTurn(BaseModel):
    """One turn in a multi-turn scenario."""

    input: str
    event_id: str | None = None
    time_offset_seconds: int = 0
    restart_before: bool = False
    duplicate_previous_event: bool = False
    concurrent_group: str | None = None
    expected: ExpectedTurn


class ShortTermScenario(BaseModel):
    """A complete multi-turn short-term-memory scenario (2-6 turns)."""

    id: str
    tags: list[str] = Field(default_factory=list)
    turns: list[ScenarioTurn] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _validate(self):
        # Reject duplicate scenario IDs (validated at collection level)
        # Reject a first-turn duplicate (the first turn has nothing to dedup against)
        if self.turns and self.turns[0].duplicate_previous_event:
            raise ValueError(f"Scenario {self.id}: first turn cannot duplicate")
        # Reject unknown turn_relation values
        known = {"continue", "revise", "switch", "new", "ambiguous", None}
        for t in self.turns:
            if t.expected.turn_relation is not None and t.expected.turn_relation not in known:
                raise ValueError(
                    f"Scenario {self.id}: unknown turn_relation {t.expected.turn_relation}"
                )
        return self


# ---------------------------------------------------------------------------
# Observable result from the runner
# ---------------------------------------------------------------------------


class ObservedScenarioTurn(BaseModel):
    """What the runner observed after a single turn."""

    result: dict[str, Any]
    replies: list[str]
    active_topic_ids: list[str]
    closed_topic_ids: list[str]
    persisted_message_count: int


__all__ = [
    "ExpectedTurn",
    "ObservedScenarioTurn",
    "ScenarioTurn",
    "ShortTermScenario",
]
