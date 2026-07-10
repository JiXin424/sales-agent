"""Integration tests: DingTalk user profile memory recall via the Online Graph.

Covers the end-to-end path from ``handle_dingtalk_event`` through the
Graph to recall user preferences when user profile memory is enabled.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository


# ====================================================================
# Online Graph runtime fixture (session-scoped, shared by all tests)
# ====================================================================


@pytest.fixture(scope="session", autouse=True)
def _online_graph_runtime():
    """Compile the Online Graph with an InMemorySaver for DingTalk tests.

    The ``handle_dingtalk_event`` path calls ``invoke_online_turn`` which
    uses ``get_online_graph()`` — without initialization that raises
    ``CheckpointUnavailableError``. We inject an InMemorySaver-backed graph
    directly into the module cache.
    """
    import sales_agent.services.online_conversation as online_conversation

    online_conversation._online_graph = online_conversation._compile_online_graph(
        InMemorySaver()
    )
    yield
    online_conversation._online_graph = None


def _patch_tenant_resolver(monkeypatch) -> None:
    """Replace TenantResolver methods with mocks that return None models.

    Avoids real API calls during integration tests.
    """
    from sales_agent.services.tenant_resolver import TenantResolver as TR

    async def fake_resolve(self, tenant_id):
        return {
            "tenant_id": tenant_id,
            "name": "Test Tenant",
            "status": "active",
            "config": {},
        }

    def fake_get_model_provider(self, tenant_info):
        return type("MockProvider", (), {"chat": None, "embedding": None})()

    monkeypatch.setattr(TR, "resolve", fake_resolve)
    monkeypatch.setattr(TR, "get_model_provider", fake_get_model_provider)


@pytest.mark.asyncio
async def test_dingtalk_new_topic_uses_preference_without_restoring_old_topic(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    _patch_tenant_resolver(monkeypatch)
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
    monkeypatch.setattr("sales_agent.services.online_conversation.get_settings", lambda: mock_settings)

    from sales_agent.integrations.dingtalk.config import DingTalkConfig

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
        DingTalkConfig(),
        mock_settings,
        type("Runtime", (), {"tenant_id": sample_tenant})(),
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


@pytest.mark.asyncio
async def test_dingtalk_transparency_lists_profile_memory(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    _patch_tenant_resolver(monkeypatch)
    scope = MemoryScope(tenant_id=sample_tenant, agent_id=active_agent.id, user_id="internal_user_1")
    memory_repo = AtomicMemoryRepository(db_session)
    await memory_repo.activate_explicit(
        scope,
        MemoryCandidate(
            memory_type="user_fact",
            normalized_key="sales_region",
            content={"key": "sales_region", "value": "华东区"},
            evidence_text="记住我负责华东区",
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

    from sales_agent.core.config import Settings as RealSettings

    mock_settings = RealSettings()
    mock_settings.long_term_memory.enabled = True
    mock_settings.user_profile_memory.enabled = True
    mock_settings.user_profile_memory.transparency_enabled = True
    mock_settings.conversation.reset_commands = ["/reset", "新话题"]
    mock_settings.guided_flows.enabled = True
    mock_settings.topic_routing.enabled = True
    mock_settings.scenario_coach.enabled = False
    monkeypatch.setattr("sales_agent.services.online_conversation.get_settings", lambda: mock_settings)

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

    from sales_agent.integrations.dingtalk.config import DingTalkConfig
    from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event

    result = await handle_dingtalk_event(
        db_session,
        DingTalkConfig(),
        mock_settings,
        type("Runtime", (), {"tenant_id": sample_tenant})(),
        event_id="profile_evt_2",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="你记得我什么",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    assert result.response_kind == "profile_transparency"
    assert any("华东区" in reply for reply in replies)
