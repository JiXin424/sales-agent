"""Integration tests: DingTalk long-term memory via the Online Graph.

Covers the end-to-end path from ``handle_dingtalk_event`` through the
Graph to an ``AtomicMemory`` row in the database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from sales_agent.models.atomic_memory import AtomicMemory


@pytest.mark.asyncio
async def test_dingtalk_explicit_remember_creates_active_memory(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    """A "记住我负责华东区" message via ``handle_dingtalk_event`` must
    create one active ``AtomicMemory`` row with ``normalized_key="sales_region"``
    and ``memory_status="success"``."""
    replies = []

    async def reply_fn(text):
        replies.append(text)

    # Minimal stubs — only what the pipeline needs to reach the Graph
    settings = type("Settings", (), {})()
    settings.conversation = type("Conversation", (), {"reset_commands": ["/reset", "新话题"]})()
    settings.long_term_memory = type("LongTermMemory", (), {"enabled": True})()

    config = type("Config", (), {})()

    runtime = type("Runtime", (), {"tenant_id": sample_tenant})()

    # Stub agent resolution so no DB binding lookup is needed
    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
        lambda db, tenant_id: active_agent.id,
    )

    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event

    result = await handle_dingtalk_event(
        db_session,
        config,
        settings,
        runtime,
        event_id="mem_evt_1",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="记住我负责华东区",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    # The DingTalk processor emits a rendered reply
    assert result.memory_operation == "remember"
    assert result.memory_status == "success"
    assert any("已记住" in reply for reply in replies)

    # Verify the DB row was created
    rows = (
        await db_session.execute(
            select(AtomicMemory).where(
                AtomicMemory.tenant_id == sample_tenant,
                AtomicMemory.agent_id == active_agent.id,
                AtomicMemory.status == "active",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].normalized_key == "sales_region"
