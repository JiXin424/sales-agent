"""Typed contracts for the Observability API client and MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReportFormat = Literal["summary", "json", "markdown", "html", "csv"]


@dataclass
class EventItem:
    sequence_no: int = 0
    event_type: str = ""
    stage: str | None = None
    status: str | None = None
    message: str = ""
    progress_current: int | None = None
    progress_total: int | None = None
    created_at: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EventItem:
        return cls(
            sequence_no=data.get("sequence_no", 0),
            event_type=data.get("event_type", ""),
            stage=data.get("stage"),
            status=data.get("status"),
            message=data.get("message", ""),
            progress_current=data.get("progress_current"),
            progress_total=data.get("progress_total"),
            created_at=data.get("created_at"),
        )


@dataclass
class WaitResult:
    events: list[EventItem] = field(default_factory=list)
    next_sequence: int = 0
    terminal: bool = False
    stage: str | None = None
    progress: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> WaitResult:
        events = [EventItem.from_api(e) for e in data.get("events", [])]
        progress = None
        last = events[-1] if events else None
        if last and last.progress_current is not None and last.progress_total is not None:
            progress = f"{last.progress_current}/{last.progress_total}"
        return cls(
            events=events,
            next_sequence=data.get("next_sequence", 0),
            terminal=data.get("terminal", False),
            stage=last.stage if last else None,
            progress=progress,
        )


@dataclass
class IterationStatus:
    id: str = ""
    agent_id: str = ""
    iteration_no: int = 0
    status: str = ""
    current_stage: str | None = None
    event_sequence: int = 0
    final_report_id: str | None = None
    recommendation: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> IterationStatus:
        return cls(
            id=data.get("id", ""),
            agent_id=data.get("agent_id", ""),
            iteration_no=data.get("iteration_no", 0),
            status=data.get("status", ""),
            current_stage=data.get("current_stage"),
            event_sequence=data.get("event_sequence", 0),
            final_report_id=data.get("final_report_id"),
            recommendation=data.get("recommendation"),
        )
