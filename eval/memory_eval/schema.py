"""Unified multi-turn scenario schema (Spec 4 §4).

This is the canonical schema for the whole memory program. It extends the
ideas in ``eval/support/dingtalk_scenario.py`` with first-class controls for
time offsets, restart, worker selection, duplicate event IDs, and concurrent
groups, plus the full set of per-turn expectations from §3.2.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TurnRelation = Literal["continue", "revise", "switch", "new", "ambiguous"]
TopicTransition = Literal["same", "new", "restored", "none"]


class ExpectedTurn(BaseModel):
    """What one turn should produce (§3.2 asserts-after-each-turn fields)."""

    response_kind: str | None = None
    turn_relation: TurnRelation | None = None
    standalone_query_contains: list[str] = Field(default_factory=list)
    retained_entities: list[str] = Field(default_factory=list)
    retracted_goals: list[str] = Field(default_factory=list)
    topic_transition: TopicTransition | None = None
    topic_id: str | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    profile_version: str | None = None
    retrieval_decision: str | None = None
    risk_decision: str | None = None
    trace_nodes: list[str] = Field(default_factory=list)
    memory_operation: str | None = None
    memory_status: str | None = None
    active_memory_keys: list[str] = Field(default_factory=list)
    active_memory_values: list[str] = Field(default_factory=list)
    candidate_count: int | None = None
    sensitive_persisted: int | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_count: int = 1


class ScenarioTurn(BaseModel):
    """One turn in a multi-turn scenario (§4 first-class controls)."""

    input: str
    event_id: str | None = None
    time_offset_seconds: int = 0
    restart_before: bool = False
    duplicate_previous_event: bool = False
    concurrent_group: str | None = None
    worker_id: str | None = None
    expected: ExpectedTurn = Field(default_factory=ExpectedTurn)


class FinalExpected(BaseModel):
    """Assertions evaluated once, after the final turn (§4 final_expected)."""

    active_topic_count: int | None = None
    cross_scope_leakage: bool | None = None
    persisted_topic_count: int | None = None
    persisted_message_count: int | None = None
    active_memory_count: int | None = None
    outbox_drained: bool | None = None
    profile_version: str | None = None


class MultiturnScenario(BaseModel):
    """A complete versioned multi-turn scenario (§4)."""

    id: str
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    turns: list[ScenarioTurn] = Field(min_length=1, max_length=6)
    final_expected: FinalExpected = Field(default_factory=FinalExpected)

    @model_validator(mode="after")
    def _validate(self) -> "MultiturnScenario":
        if self.turns and self.turns[0].duplicate_previous_event:
            raise ValueError(f"Scenario {self.id}: first turn cannot duplicate")
        return self


class ObservedTurn(BaseModel):
    """What the scenario runner observed after a single turn."""

    turn_index: int
    result: dict[str, Any]
    replies: list[str]
    active_topic_ids: list[str]
    closed_topic_ids: list[str]
    active_memory_keys: list[str]
    selected_memory_ids: list[str]
    profile_version: str | None = None
    duplicate: bool = False
    error: str | None = None


class ScenarioRun(BaseModel):
    """Full observed run of one scenario."""

    scenario_id: str
    observed: list[ObservedTurn]
    final_state: dict[str, Any]
    error: str | None = None


__all__ = [
    "ExpectedTurn",
    "FinalExpected",
    "MultiturnScenario",
    "ObservedTurn",
    "ScenarioRun",
    "ScenarioTurn",
    "TurnRelation",
]
