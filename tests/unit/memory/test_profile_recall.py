from datetime import datetime, timezone

import pytest

from sales_agent.services.memory.contracts import AtomicMemoryRecord, MemoryScope
from sales_agent.services.memory.profile_recall import (
    eligible_memory_types_for_task,
    format_user_memory_context,
    rank_recall_items,
)


def _record(memory_id, memory_type, key, value, evidence_count=1):
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    return AtomicMemoryRecord(
        id=memory_id,
        scope=MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1"),
        memory_type=memory_type,
        normalized_key=key,
        content={"key": key, "value": value},
        search_text=value,
        status="active",
        source_kind="explicit_user",
        source_conversation_id="conv1",
        source_message_ids=[f"msg-{memory_id}"],
        evidence_count=evidence_count,
        confidence_band="confirmed",
        sensitivity="normal",
        observed_at=now,
        last_confirmed_at=now,
        expires_at=None,
    )


def test_task_eligibility_keeps_product_facts_out_of_profile_recall():
    assert "response_preference" in eligible_memory_types_for_task("knowledge_qa", "required")
    assert "user_fact" not in eligible_memory_types_for_task("knowledge_qa", "required")
    assert "coaching_goal" in eligible_memory_types_for_task("general_sales_coaching", "none")


def test_rank_recall_items_enforces_diversity_and_top_five():
    records = [
        _record("m1", "response_preference", "response_style", "回答短一点", 1),
        _record("m2", "coaching_goal", "coaching_goal", "提升异议处理", 2),
        _record("m3", "recurring_challenge", "recurring_challenge", "价格异议容易卡住", 2),
        _record("m4", "sales_pattern", "sales_pattern", "先确认预算", 1),
        _record("m5", "user_fact", "sales_region", "华东区", 1),
        _record("m6", "user_fact", "product_focus", "电影票", 1),
    ]

    ranked = rank_recall_items(records, standalone_query="帮我写一段简洁的价格异议话术", max_items=5)

    assert len(ranked) == 5
    assert ranked[0].memory_id in {"m2", "m3"}
    assert len({item.normalized_key for item in ranked}) == 5


def test_format_user_memory_context_respects_item_and_char_budget():
    records = [
        _record(f"m{i}", "response_preference", f"style_{i}", "回答短一点" * 80)
        for i in range(8)
    ]
    items = rank_recall_items(records, standalone_query="简洁回复", max_items=8)
    text = format_user_memory_context(items, max_items=5, max_chars=1200)

    assert text.startswith("USER_MEMORY_CONTEXT")
    assert text.endswith("END_USER_MEMORY_CONTEXT")
    assert text.count("- memory_id:") <= 5
    assert len(text) <= 1200
