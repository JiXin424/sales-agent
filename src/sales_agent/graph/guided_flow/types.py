from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FlowStart:
    stage: str
    payload: dict[str, Any]
    reply: str


@dataclass(frozen=True)
class FlowAdvance:
    stage: str
    payload: dict[str, Any]
    reply: str
    completed: bool


@dataclass(frozen=True)
class FlowServices:
    db: Any
    chat_model: Any
    tenant_id: str
    agent_id: str


StartFlow = Callable[[], FlowStart]
AdvanceFlow = Callable[[str, dict[str, Any], str, FlowServices], Awaitable[FlowAdvance]]


@dataclass(frozen=True)
class FlowDefinition:
    id: str
    label: str
    trigger_phrases: frozenset[str]
    start: StartFlow
    advance: AdvanceFlow
    metadata: dict[str, str] = field(default_factory=dict)
