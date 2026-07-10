from __future__ import annotations

import re
from datetime import datetime, timedelta

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryWriteDecision

_PROHIBITED_PATTERNS = [
    re.compile(r"(密码|口令|token|api[_-]?key|access[_-]?key)", re.I),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(银行卡|信用卡|医保|病历|宗教|政治立场)"),
    re.compile(r"(手机号|电话|邮箱|住址|家庭地址)"),
]

_TEMPORARY_PATTERNS = [
    re.compile(r"(今天|明天|这次|刚才|临时|一会儿|这个客户|这单)"),
]


def classify_sensitivity(text: str) -> str:
    if any(pattern.search(text) for pattern in _PROHIBITED_PATTERNS):
        return "prohibited"
    return "normal"


def classify_stability(text: str) -> str:
    if any(pattern.search(text) for pattern in _TEMPORARY_PATTERNS):
        return "temporary"
    return "stable"


def default_expires_at(memory_type: str, now: datetime) -> datetime | None:
    if memory_type == "response_preference":
        return None
    if memory_type == "user_fact":
        return now + timedelta(days=180)
    if memory_type in {"coaching_goal", "sales_pattern", "recurring_challenge"}:
        return now + timedelta(days=90)
    return now + timedelta(days=90)


def decide_candidate_write(
    candidate: MemoryCandidate,
    *,
    existing_evidence_count: int,
) -> MemoryWriteDecision:
    if candidate.sensitivity == "prohibited":
        return MemoryWriteDecision("reject", "rejected", "prohibited_sensitivity")
    if candidate.stability != "stable":
        return MemoryWriteDecision("reject", "rejected", "not_stable")
    if candidate.source_kind == "explicit_user" and candidate.confidence_band == "confirmed":
        return MemoryWriteDecision("activate", "active", "explicit_confirmed")
    if candidate.source_kind == "verified_tool" and candidate.confidence_band in {"confirmed", "corroborated"}:
        return MemoryWriteDecision("activate", "active", "verified_tool_confirmed")
    if candidate.source_kind == "inferred_user" and existing_evidence_count >= 1:
        return MemoryWriteDecision("activate", "active", "corroborated_two_evidence")
    if candidate.source_kind == "inferred_user":
        return MemoryWriteDecision("candidate", "candidate", "needs_second_evidence")
    return MemoryWriteDecision("reject", "rejected", "unsupported_source")
