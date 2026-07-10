from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryOperationResult, MemoryScope
from sales_agent.services.memory.policy import classify_sensitivity, classify_stability
from sales_agent.services.memory.repository import AtomicMemoryRepository


@dataclass(frozen=True)
class MemoryCommand:
    operation: Literal["remember", "correct", "forget"]
    normalized_key: str | None
    value: str | None
    raw_text: str
    confirm_broad: bool = False


_REGION_VALUE = r"(华东区|华南区|华北区|华中区|西南区|东北区|华东|华南|华北|华中|西南|东北)"


def detect_memory_command(text: str) -> MemoryCommand | None:
    stripped = text.strip()
    if stripped == "确认忘记全部":
        return MemoryCommand("forget", None, None, stripped, confirm_broad=True)

    if re.search(r"忘记.*(所有|全部|关于我)", stripped):
        return MemoryCommand("forget", None, None, stripped, confirm_broad=False)

    if re.search(r"忘记.*(区域|负责)", stripped):
        return MemoryCommand("forget", "sales_region", None, stripped, confirm_broad=False)

    correction = re.search(r"(不负责|不再负责).*(现在|改成|变成|负责)\s*" + _REGION_VALUE, stripped)
    if correction:
        return MemoryCommand("correct", "sales_region", correction.group(3), stripped)

    remember_region = re.search(r"(记住|帮我记住|以后记得).*(负责)\s*" + _REGION_VALUE, stripped)
    if remember_region:
        return MemoryCommand("remember", "sales_region", remember_region.group(3), stripped)

    remember_style = re.search(r"(记住|帮我记住|以后记得).*(回答|回复).*(短一点|简洁|详细|表格)", stripped)
    if remember_style:
        return MemoryCommand("remember", "response_style", remember_style.group(3), stripped)

    return None


def _candidate_from_command(command: MemoryCommand) -> MemoryCandidate:
    if command.value is None or command.normalized_key is None:
        raise ValueError("remember/correct command requires a value and normalized_key")
    memory_type = "response_preference" if command.normalized_key == "response_style" else "user_fact"
    sensitivity = classify_sensitivity(command.raw_text)
    stability = classify_stability(command.raw_text)
    return MemoryCandidate(
        memory_type=memory_type,
        normalized_key=command.normalized_key,
        content={"key": command.normalized_key, "value": command.value},
        evidence_text=command.raw_text,
        source_kind="explicit_user",
        stability=stability,
        sensitivity=sensitivity,
        confidence_band="confirmed",
    )


async def apply_memory_command(
    *,
    repo: AtomicMemoryRepository,
    scope: MemoryScope,
    command: MemoryCommand,
    conversation_id: str,
    message_id: str,
    now: datetime | None = None,
) -> MemoryOperationResult:
    if command.operation == "forget":
        return await repo.forget_memory(
            scope,
            normalized_key=command.normalized_key,
            confirm_broad=command.confirm_broad,
        )

    candidate = _candidate_from_command(command)
    if candidate.sensitivity == "prohibited":
        return MemoryOperationResult(
            operation=command.operation,
            status="rejected",
            response_text="这类信息不适合保存为长期记忆，我不会记录。",
            reason_code="prohibited_sensitivity",
        )
    if candidate.stability != "stable":
        return MemoryOperationResult(
            operation=command.operation,
            status="rejected",
            response_text="这看起来是临时信息，我不会保存为长期记忆。",
            reason_code="not_stable",
        )

    if command.operation == "remember":
        return await repo.activate_explicit(
            scope,
            candidate,
            conversation_id=conversation_id,
            message_id=message_id,
            now=now,
        )

    return await repo.correct_memory(
        scope,
        normalized_key=candidate.normalized_key,
        new_candidate=candidate,
        conversation_id=conversation_id,
        message_id=message_id,
        now=now,
    )
