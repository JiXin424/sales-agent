from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

from sales_agent.services.memory.contracts import AtomicMemoryRecord
from sales_agent.services.memory.profile_contracts import EMPTY_PROFILE, ProfileProjectionResult


_SINGLE_VALUE_PATHS = {
    "role": "work_context.role",
    "sales_region": "work_context.sales_region",
    "verbosity": "response_preferences.verbosity",
    "coaching_style": "response_preferences.coaching_style",
}

_LIST_VALUE_PATHS = {
    "product_focus": "work_context.product_focus",
    "response_format": "response_preferences.format",
    "coaching_goal": "development.coaching_goals",
    "recurring_challenge": "development.recurring_challenges",
    "sales_pattern": "development.confirmed_sales_patterns",
}


def _value(record: AtomicMemoryRecord) -> str:
    raw = record.content.get("value")
    if raw is None:
        raw = record.search_text
    return str(raw).strip()


def _last_confirmed(record: AtomicMemoryRecord) -> datetime:
    return record.last_confirmed_at or record.observed_at


def _set_path(profile: dict, dotted_path: str, value):
    target = profile
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def compute_source_memory_version(records: Iterable[AtomicMemoryRecord]) -> str:
    active = sorted(
        (r for r in records if r.status == "active"),
        key=lambda r: (r.id, _last_confirmed(r).isoformat()),
    )
    if not active:
        return "empty"
    material = [
        {
            "id": r.id,
            "status": r.status,
            "normalized_key": r.normalized_key,
            "evidence_count": r.evidence_count,
            "last_confirmed_at": _last_confirmed(r).isoformat(),
        }
        for r in active
    ]
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:16]


def project_user_profile(
    records: list[AtomicMemoryRecord],
    *,
    now: datetime | None = None,
) -> ProfileProjectionResult:
    current = now or datetime.now(timezone.utc)
    profile = json.loads(json.dumps(EMPTY_PROFILE, ensure_ascii=False))
    evidence_map: dict[str, list[str]] = {}
    active = [
        r for r in records
        if r.status == "active"
        and (r.expires_at is None or r.expires_at > current)
        and r.sensitivity == "normal"
    ]

    for key, path in _SINGLE_VALUE_PATHS.items():
        candidates = [r for r in active if r.normalized_key == key]
        if not candidates:
            continue
        chosen = sorted(candidates, key=lambda r: (_last_confirmed(r), r.evidence_count), reverse=True)[0]
        value = _value(chosen)
        if value:
            _set_path(profile, path, value)
            evidence_map[path] = [chosen.id]

    for key, path in _LIST_VALUE_PATHS.items():
        candidates = [r for r in active if r.normalized_key == key or r.memory_type == key]
        ordered = sorted(candidates, key=lambda r: (r.evidence_count, _last_confirmed(r)), reverse=True)
        values: list[str] = []
        ids: list[str] = []
        seen: set[str] = set()
        for record in ordered:
            value = _value(record)
            if not value or value in seen:
                continue
            values.append(value)
            ids.append(record.id)
            seen.add(value)
        if values:
            _set_path(profile, path, values)
            evidence_map[path] = ids

    return ProfileProjectionResult(
        profile=profile,
        evidence_map=evidence_map,
        source_memory_version=compute_source_memory_version(active),
        generated_at=current,
    )
