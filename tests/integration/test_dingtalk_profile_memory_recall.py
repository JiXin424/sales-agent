"""Integration tests: DingTalk user profile memory recall via the Online Graph.

Covers the end-to-end path from ``handle_dingtalk_event`` through the
Graph to recall user preferences when user profile memory is enabled.
"""

from __future__ import annotations

import pytest

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository


@pytest.mark.asyncio
async def test_dingtalk_new_topic_uses_preference_without_restoring_old_topic(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    scope = MemoryScope(tenant_id=sample_tenant, agent_id=active_agent.id, user_id="internal_user_1")
    memory_repo = AtomicMemoryRepository(db_session)
    await memory_repo.activate_explicit(
        scope,
        MemoryCandidate(
            memory_type="response_preference",
            normalized_key="response_style",
            content={"key": "response_style", "value": "回答短一点"},
            evidence_text="记住以后回答短一点",
            source_kind="explicit_user",
            stability="stable",
            sensitivity="normal",
            confidence_band="confirmed",
        ),
        conversation_id="conv1",
        message_id="msg1",
    )
    await UserMemoryProfileRepository(db_session).rebuild_profile_for_scope(scope)

    replies = []

    async def reply_fn(text):
        replies.append(text)

    # Ensure user_profile_memory is enabled in the global config so the
    # graph pipeline (which calls get_settings() internally) picks it up.
    from sales_agent.core.config import Settings as RealSettings

    mock_settings = RealSettings()
    mock_settings.user_profile_memory.enabled = True
    mock_settings.user_profile_memory.recall_enabled = True
    mock_settings.user_profile_memory.max_recall_items = 5
    mock_settings.user_profile_memory.max_recall_chars = 1200
    mock_settings.long_term_memory.enabled = True
    mock_settings.conversation.reset_commands = ["/reset", "新话题"]
    mock_settings.guided_flows.enabled = True
    mock_settings.topic_routing.enabled = True
    mock_settings.scenario_coach.enabled = False
    monkeypatch.setattr("sales_agent.core.config.get_settings", lambda: mock_settings)

    runtime = type("Runtime", (), {"tenant_id": sample_tenant})()
    config = type("Config", (), {})()

    async def fake_get_or_create_user(self, corp_id, dingtalk_user_id, display_name):
        return "internal_user_1"

    async def fake_resolve_agent_id(db, tenant_id):
        return active_agent.id

    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper.get_or_create_user",
        fake_get_or_create_user,
    )
    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
        fake_resolve_agent_id,
    )

    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event

    result = await handle_dingtalk_event(
        db_session,
        config,
        mock_settings,
        runtime,
        event_id="profile_evt_1",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="新话题 帮我写一段跟进话术",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    assert result.selected_memory_ids
    assert result.memory_degraded is False
    assert result.turn_relation in {"new", None}
