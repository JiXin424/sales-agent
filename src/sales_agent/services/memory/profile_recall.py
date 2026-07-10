from __future__ import annotations

import time
from datetime import datetime, timezone

from sales_agent.services.memory.contracts import AtomicMemoryRecord, MemoryScope
from sales_agent.services.memory.profile_contracts import RecallItem, RecallResult, RecallTrace
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
from sales_agent.services.memory.repository import AtomicMemoryRepository


def eligible_memory_types_for_task(task_type: str | None, knowledge_policy: str | None) -> list[str]:
    if task_type in {"help", "reset"}:
        return []
    if knowledge_policy == "required":
        return ["response_preference"]
    if task_type in {"knowledge_qa"}:
        return ["response_preference"]
    if task_type in {"script_generation", "objection_handling", "general_sales_coaching"}:
        return [
            "response_preference",
            "coaching_goal",
            "recurring_challenge",
            "sales_pattern",
            "user_fact",
        ]
    return ["response_preference", "coaching_goal", "recurring_challenge"]


def _value(record: AtomicMemoryRecord) -> str:
    return str(record.content.get("value") or record.search_text).strip()


def _last_confirmed(record: AtomicMemoryRecord) -> datetime:
    return record.last_confirmed_at or record.observed_at


def _lexical_relevance(record: AtomicMemoryRecord, query: str) -> int:
    text = (_value(record) + " " + record.normalized_key).lower()
    return sum(1 for char in set(query.lower()) if char and char in text)


def rank_recall_items(
    records: list[AtomicMemoryRecord],
    *,
    standalone_query: str,
    max_items: int,
) -> list[RecallItem]:
    active = [
        r for r in records
        if r.status == "active"
        and r.sensitivity == "normal"
        and (r.expires_at is None or r.expires_at > datetime.now(timezone.utc))
    ]
    ordered = sorted(
        active,
        key=lambda r: (
            _lexical_relevance(r, standalone_query),
            r.evidence_count,
            _last_confirmed(r),
        ),
        reverse=True,
    )
    selected: list[RecallItem] = []
    used_keys: set[str] = set()
    for record in ordered:
        if record.normalized_key in used_keys:
            continue
        text = _value(record)
        if not text:
            continue
        selected.append(
            RecallItem(
                memory_id=record.id,
                memory_type=record.memory_type,
                normalized_key=record.normalized_key,
                text=text,
                last_confirmed_at=record.last_confirmed_at,
                evidence_count=record.evidence_count,
            )
        )
        used_keys.add(record.normalized_key)
        if len(selected) >= max_items:
            break
    return selected


def format_user_memory_context(
    items: list[RecallItem],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    if not items:
        return ""
    lines = ["USER_MEMORY_CONTEXT"]
    for item in items[:max_items]:
        confirmed = item.last_confirmed_at.isoformat() if item.last_confirmed_at else ""
        lines.extend([
            f"- memory_id: {item.memory_id}",
            f"  memory_type: {item.memory_type}",
            f"  normalized_key: {item.normalized_key}",
            f"  fact_or_preference: {item.text}",
            f"  last_confirmed_at: {confirmed}",
        ])
    lines.append("END_USER_MEMORY_CONTEXT")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len("\nEND_USER_MEMORY_CONTEXT")] + "\nEND_USER_MEMORY_CONTEXT"


async def retrieve_user_memory_context(
    *,
    db,
    scope: MemoryScope,
    standalone_query: str,
    task_type: str | None,
    knowledge_policy: str | None,
    max_items: int,
    max_chars: int,
) -> RecallResult:
    start = time.time()
    trace = RecallTrace()
    eligible_types = eligible_memory_types_for_task(task_type, knowledge_policy)
    trace.eligible_memory_types = eligible_types
    if not eligible_types:
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text="", selected_items=[], trace=trace)

    try:
        profile_repo = UserMemoryProfileRepository(db)
        profile = await profile_repo.get_current_profile(scope)
        if profile is None:
            await profile_repo.enqueue_profile_rebuild(scope, reason="profile_missing", source_memory_id=None)
        else:
            trace.profile_version = profile.version

        memory_repo = AtomicMemoryRepository(db)
        records = await memory_repo.list_active_memories(scope)
        filtered = [record for record in records if record.memory_type in eligible_types]
        trace.candidate_count = len(filtered)
        selected = rank_recall_items(filtered, standalone_query=standalone_query, max_items=max_items)
        context_text = format_user_memory_context(selected, max_items=max_items, max_chars=max_chars)
        trace.selected_memory_ids = [item.memory_id for item in selected]
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text=context_text, selected_items=selected, trace=trace)
    except Exception as exc:
        trace.degraded = True
        trace.degradation_reason = exc.__class__.__name__
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text="", selected_items=[], trace=trace)
