"""Unit tests: all DingTalk paths route through the Online Conversation Graph.

Asserts:

- Processor passes ``session_user_id=sender_id``, internal ``user_id``,
  ``event_id`` and resolved Agent ID to ``invoke_online_turn``.
- Streaming path uses ``get_online_graph`` (not ``build_chat_graph_compiled``).
- Quick-entry ``_fulfill_quick_action`` passes ``entry_action`` to
  ``invoke_online_turn`` and never accesses legacy ``QuickSession``.
- ``resolve_dingtalk_agent_id`` returns the bound Agent when present,
  otherwise the tenant default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ====================================================================
# resolve_dingtalk_agent_id
# ====================================================================


class TestResolveDingtalkAgentId:
    @pytest.mark.asyncio
    async def test_returns_bound_active_agent(self):
        """When a configured channel binding exists on an active Agent,
        return that Agent's ID."""
        mock_agent = MagicMock()
        mock_agent.id = "bound_agent_001"
        mock_agent.status = "active"

        mock_config = MagicMock()
        mock_config.agent_id = "bound_agent_001"

        mock_db = AsyncMock()

        async def _execute_side_effect(*args, **kwargs):
            """Return a result object whose scalar_one_or_none() is
            sync (not a coroutine), matching SQLAlchemy's synchronous
            scalar_one_or_none on an awaitable execute()."""
            if not hasattr(_execute_side_effect, "call_count"):
                _execute_side_effect.call_count = 0
            _execute_side_effect.call_count += 1
            result = MagicMock()
            if _execute_side_effect.call_count == 1:
                # First query: AgentChannelConfig row found
                result.scalar_one_or_none.return_value = mock_config
            else:
                # Second query: Agent is active
                result.scalar_one_or_none.return_value = mock_agent
            return result

        mock_db.execute = _execute_side_effect

        from sales_agent.integrations.dingtalk.agent_resolver import (
            resolve_dingtalk_agent_id,
        )

        result = await resolve_dingtalk_agent_id(mock_db, "tenant_001")
        assert result == "bound_agent_001"

    @pytest.mark.asyncio
    async def test_falls_back_to_default_agent_when_no_channel_binding(self):
        """When no AgentChannelConfig row exists, fall back to
        resolve_tenant_agent_id."""

        async def _execute_no_binding(*args, **kwargs):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = _execute_no_binding

        mock_default_agent = MagicMock()
        mock_default_agent.id = "default_agent_001"

        with patch(
            "sales_agent.integrations.dingtalk.agent_resolver.resolve_tenant_agent_id",
            new_callable=AsyncMock,
            return_value=mock_default_agent,
        ) as mock_resolve:
            from sales_agent.integrations.dingtalk.agent_resolver import (
                resolve_dingtalk_agent_id,
            )

            result = await resolve_dingtalk_agent_id(mock_db, "tenant_001")
            assert result == "default_agent_001"
            mock_resolve.assert_awaited_once_with(mock_db, "tenant_001", None)

    @pytest.mark.asyncio
    async def test_falls_back_when_bound_agent_is_not_active(self):
        """If the channel binding points to a paused/archived Agent,
        fall back to the tenant default."""
        mock_paused_agent = MagicMock()
        mock_paused_agent.id = "paused_agent_001"
        mock_paused_agent.status = "paused"

        mock_config = MagicMock()
        mock_config.agent_id = "paused_agent_001"

        async def _execute_side_effect(*args, **kwargs):
            if not hasattr(_execute_side_effect, "call_count"):
                _execute_side_effect.call_count = 0
            _execute_side_effect.call_count += 1
            result = MagicMock()
            if _execute_side_effect.call_count == 1:
                result.scalar_one_or_none.return_value = mock_config
            else:
                result.scalar_one_or_none.return_value = mock_paused_agent
            return result

        mock_db = AsyncMock()
        mock_db.execute = _execute_side_effect

        mock_default_agent = MagicMock()
        mock_default_agent.id = "default_agent_002"

        with patch(
            "sales_agent.integrations.dingtalk.agent_resolver.resolve_tenant_agent_id",
            new_callable=AsyncMock,
            return_value=mock_default_agent,
        ) as mock_resolve:
            from sales_agent.integrations.dingtalk.agent_resolver import (
                resolve_dingtalk_agent_id,
            )

            result = await resolve_dingtalk_agent_id(mock_db, "tenant_001")
            assert result == "default_agent_002"


# ====================================================================
# Processor routing
# ====================================================================


class TestProcessorRouting:
    """The processor's ``handle_dingtalk_event`` must call
    ``invoke_online_turn`` with the correct parameters instead of
    building the Chat Graph directly."""

    @pytest.mark.asyncio
    async def test_invoke_online_turn_called_with_correct_params(self):
        reply_fn = AsyncMock()

        mock_db = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.conversation.reset_commands = ["reset", "/reset", "新话题"]
        mock_runtime = MagicMock()
        mock_runtime.tenant_id = "test_tenant"
        mock_config = MagicMock()

        # Module-level imports in processor.py must be patched on the
        # processor module itself; lazy imports (inside the function body)
        # are patched on the source module.
        with patch(
            "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
            new_callable=AsyncMock,
            return_value="agent_123",
        ) as mock_resolve:
            with patch(
                "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
                new_callable=AsyncMock,
                return_value={
                    "answer_dict": {"summary": "Hello!", "sections": []},
                    "response_kind": "chat",
                },
            ) as mock_invoke:
                with patch(
                    "sales_agent.integrations.dingtalk.processor.DingTalkMessageRenderer",
                ) as mock_renderer_cls:
                    renderer_instance = MagicMock()
                    renderer_instance.render.return_value = "Rendered text"
                    mock_renderer_cls.return_value = renderer_instance

                    with patch(
                        "sales_agent.integrations.dingtalk.command_parser.DingTalkCommandParser",
                    ) as mock_parser_cls:
                        parser_instance = MagicMock()
                        parser_instance.parse.return_value.is_reset = False
                        mock_parser_cls.return_value = parser_instance

                        with patch(
                            "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper",
                        ) as mock_user_mapper_cls:
                            user_mapper_instance = AsyncMock()
                            user_mapper_instance.get_or_create_user.return_value = "internal_user_001"
                            mock_user_mapper_cls.return_value = user_mapper_instance

                            with patch(
                                "sales_agent.integrations.dingtalk.conversation_mapper.DingTalkConversationMapper",
                            ) as mock_conv_mapper_cls:
                                conv_instance = MagicMock()
                                conv_instance.generate_conversation_id.return_value = "conv_001"
                                mock_conv_mapper_cls.return_value = conv_instance

                                from sales_agent.integrations.dingtalk.processor import (
                                    handle_dingtalk_event,
                                )

                                await handle_dingtalk_event(
                                    db=mock_db,
                                    config=mock_config,
                                    settings=mock_settings,
                                    runtime=mock_runtime,
                                    event_id="event_001",
                                    corp_id="corp_001",
                                    sender_id="ding_user_001",
                                    sender_name="Test User",
                                    message_type="text",
                                    text="hello",
                                    dingtalk_conversation_id="conv_001",
                                    reply_fn=reply_fn,
                                )

        # Verify invoke_online_turn was called with correct params
        mock_invoke.assert_awaited_once()
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["session_user_id"] == "ding_user_001"
        assert call_kwargs["user_id"] == "internal_user_001"
        assert call_kwargs["tenant_id"] == "test_tenant"
        assert call_kwargs["agent_id"] == "agent_123"
        assert call_kwargs["event_id"] == "event_001"
        assert call_kwargs["channel"] == "dingtalk"

        # Verify reply was sent with rendered text
        reply_fn.assert_awaited_once_with("Rendered text")

    @pytest.mark.asyncio
    async def test_invoke_online_turn_not_called_for_fallback_message(self):
        """Fallback messages (type 'fallback') should skip the graph entirely."""
        reply_fn = AsyncMock()

        with patch(
            "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
            new_callable=AsyncMock,
        ) as mock_invoke:
            from sales_agent.integrations.dingtalk.processor import (
                handle_dingtalk_event,
            )

            mock_db = AsyncMock()
            mock_config = MagicMock()
            mock_settings = MagicMock()
            mock_runtime = MagicMock()

            await handle_dingtalk_event(
                db=mock_db,
                config=mock_config,
                settings=mock_settings,
                runtime=mock_runtime,
                event_id="event_002",
                corp_id="corp_001",
                sender_id="user_001",
                sender_name="User",
                message_type="fallback",
                text="Service temporarily unavailable",
                dingtalk_conversation_id="conv_001",
                reply_fn=reply_fn,
            )

            mock_invoke.assert_not_awaited()
            reply_fn.assert_awaited_once_with("Service temporarily unavailable")


# ====================================================================
# Streaming path routing
# ====================================================================


class TestStreamingPath:
    """The streaming handler must use ``get_online_graph`` instead of
    ``build_chat_graph_compiled`` and include online state fields."""

    @pytest.mark.asyncio
    async def test_get_online_graph_called_instead_of_chat_graph(self):
        """Verify that ``handle_dingtalk_stream_via_graph`` uses
        ``get_online_graph`` with online state fields."""
        # Mock the graph to behave like an async iterable producing updates
        async def _fake_astream(*args, **kwargs):
            yield ("updates", {"normalize_turn": {"flow_action": "chat"}})
            yield (
                "updates",
                {"chat": {"answer_dict": {"summary": "Streamed reply", "sections": []}}},
            )

        mock_graph = MagicMock()
        mock_graph.astream = _fake_astream

        mock_card_sender = AsyncMock()
        mock_card_sender.send_markdown_card.return_value = "card_001"

        from unittest.mock import AsyncMock as AMock

        reply_fn = AMock()

        # Mock get_settings to return guided_flows enabled
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = True

        mock_checkpointer = MagicMock()

        with patch(
            "sales_agent.integrations.dingtalk.graph_stream.get_checkpointer",
            return_value=mock_checkpointer,
        ):
            with patch(
                "sales_agent.integrations.dingtalk.graph_stream.get_settings",
                return_value=mock_settings,
            ):
                with patch(
                    "sales_agent.integrations.dingtalk.graph_stream.get_online_graph",
                    return_value=mock_graph,
                ) as mock_get_online:
                    from sales_agent.integrations.dingtalk.graph_stream import (
                        handle_dingtalk_stream_via_graph,
                    )

                    result = await handle_dingtalk_stream_via_graph(
                        tenant_id="test_tenant",
                        user_id="internal_user_001",
                        dingtalk_user_id="ding_user_001",
                        message="Test message",
                        conversation_id="conv_001",
                        agent_id="agent_123",
                        event_id="event_001",
                        reply_fn=reply_fn,
                        card_sender=mock_card_sender,
                        db=MagicMock(),
                        chat_model=MagicMock(),
                        embedding_model=MagicMock(),
                    )

        # Must have used get_online_graph
        mock_get_online.assert_called_once()
        # Result should contain the answer_dict from the stream
        assert "answer_dict" in result
        assert result["answer_dict"]["summary"] == "Streamed reply"

    @pytest.mark.asyncio
    async def test_streaming_includes_online_state_fields(self):
        """The input state passed to the online graph should include
        session_user_id, event_id, and guided_flows_enabled."""
        captured_state = {}

        async def _fake_astream(input_state, config, context=None, stream_mode=None):
            nonlocal captured_state
            captured_state = input_state
            yield ("updates", {"normalize_turn": {"flow_action": "chat"}})
            yield (
                "updates",
                {"chat": {"answer_dict": {"summary": "Result", "sections": []}}},
            )

        mock_graph = MagicMock()
        mock_graph.astream = _fake_astream

        mock_card_sender = AsyncMock()
        mock_card_sender.send_markdown_card.return_value = "card_002"

        reply_fn = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = True

        with patch(
            "sales_agent.integrations.dingtalk.graph_stream.get_checkpointer",
        ):
            with patch(
                "sales_agent.integrations.dingtalk.graph_stream.get_settings",
                return_value=mock_settings,
            ):
                with patch(
                    "sales_agent.integrations.dingtalk.graph_stream.get_online_graph",
                    return_value=mock_graph,
                ):
                    from sales_agent.integrations.dingtalk.graph_stream import (
                        handle_dingtalk_stream_via_graph,
                    )

                    await handle_dingtalk_stream_via_graph(
                        tenant_id="test_tenant",
                        user_id="internal_user_001",
                        dingtalk_user_id="ding_user_001",
                        message="Test",
                        conversation_id="conv_001",
                        agent_id="agent_123",
                        event_id="event_001",
                        reply_fn=reply_fn,
                        card_sender=mock_card_sender,
                        db=MagicMock(),
                        chat_model=MagicMock(),
                    )

        assert captured_state.get("session_user_id") == "ding_user_001"
        assert captured_state.get("event_id") == "event_001"
        assert captured_state.get("guided_flows_enabled") is True
        # Should NOT include skip_generation (removed from online state)
        assert "skip_generation" not in captured_state
        # The channel should be "dingtalk"
        assert captured_state.get("channel") == "dingtalk"
        assert captured_state.get("agent_id") == "agent_123"


# ====================================================================
# Quick entry routing
# ====================================================================


class TestQuickEntryRouting:
    """Quick entry ``_fulfill_quick_action`` must route through
    ``invoke_online_turn`` and never use ``QuickSession``."""

    @pytest.mark.asyncio
    async def test_fulfill_quick_action_calls_invoke_online_turn(self):
        """For a quick action, _fulfill_quick_action must call
        invoke_online_turn with entry_action, message='', and
        never access QuickSession/start_session."""
        mock_sender = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = True

        mock_db_session = AsyncMock()
        # Make commit a regular mock that can be awaited
        mock_db_session.commit = AsyncMock()
        mock_db_session.rollback = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__.return_value = mock_db_session

        action_config = {
            "flow_id": "pre_visit_prepare",
            "task_type": "visit_preparation",
            "label": "访前准备",
            "subtitle": "1 分钟生成客户沟通作战卡",
            "message_icon": "ud83dudccb",
        }

        with patch(
            "sales_agent.integrations.dingtalk.quick_entry.get_settings",
            return_value=mock_settings,
        ):
            with patch(
                "sales_agent.core.database.get_session_factory",
                return_value=mock_factory,
            ):
                with patch(
                    "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper",
                ) as mock_mapper_cls:
                    mock_mapper = AsyncMock()
                    mock_mapper.get_or_create_user.return_value = "internal_user_001"
                    mock_mapper_cls.return_value = mock_mapper

                    with patch(
                        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                        new_callable=AsyncMock,
                        return_value="agent_123",
                    ) as mock_resolve:
                        with patch(
                            "sales_agent.services.online_conversation.invoke_online_turn",
                            new_callable=AsyncMock,
                            return_value={
                                "answer_dict": {
                                    "summary": "请告诉我以下信息：\n1. 你要见谁？\n2. 客户什么情况？",
                                    "sections": [],
                                },
                                "response_kind": "start",
                            },
                        ) as mock_invoke:
                            # Mock _get_dingtalk_config (module-level in quick_entry)
                            mock_dt_config = MagicMock()
                            mock_dt_config.corp_id = "corp_test_001"
                            with patch(
                                "sales_agent.integrations.dingtalk.quick_entry._get_dingtalk_config",
                                return_value=mock_dt_config,
                            ):
                                from sales_agent.integrations.dingtalk.quick_entry import (
                                    _fulfill_quick_action,
                                )

                                result = await _fulfill_quick_action(
                                    sender=mock_sender,
                                    dingtalk_user_id="ding_user_001",
                                    action="pre_visit_prepare",
                                    action_config=action_config,
                                    tenant_id="test_tenant",
                                )

            # Verify invoke_online_turn was called correctly
            mock_invoke.assert_awaited_once()
            invoke_kwargs = mock_invoke.call_args[1]
            assert invoke_kwargs["entry_action"] == "visit_preparation"
            assert invoke_kwargs["message"] == ""
            assert invoke_kwargs["channel"] == "dingtalk"
            assert invoke_kwargs["session_user_id"] == "ding_user_001"
            assert invoke_kwargs["agent_id"] == "agent_123"

            # Verify sender received the summary
            mock_sender.send_text.assert_awaited_once()
            sent_text = mock_sender.send_text.call_args[0][1]
            assert "请告诉我以下信息" in sent_text

            # Verify result
            assert result["status"] == "ok"
            assert result["action"] == "pre_visit_prepare"

    @pytest.mark.asyncio
    async def test_guided_flows_disabled_returns_disabled_message(self):
        """When guided_flows.enabled is False, return a clear
        '该引导功能暂时停用' message and do not invoke online graph."""
        mock_sender = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = False

        action_config = {
            "flow_id": "pre_visit_prepare",
            "task_type": "visit_preparation",
            "label": "访前准备",
            "subtitle": "",
            "message_icon": "ud83dudccb",
        }

        with patch(
            "sales_agent.integrations.dingtalk.quick_entry.get_settings",
            return_value=mock_settings,
        ):
            with patch(
                "sales_agent.services.online_conversation.invoke_online_turn",
                new_callable=AsyncMock,
            ) as mock_invoke:
                from sales_agent.integrations.dingtalk.quick_entry import (
                    _fulfill_quick_action,
                )

                result = await _fulfill_quick_action(
                    sender=mock_sender,
                    dingtalk_user_id="ding_user_001",
                    action="pre_visit_prepare",
                    action_config=action_config,
                    tenant_id="test_tenant",
                )

                # Online graph should NOT be called
                mock_invoke.assert_not_awaited()

        # Sender should get the disabled message
        mock_sender.send_text.assert_awaited_once_with(
            "ding_user_001", "该引导功能暂时停用"
        )
        assert result["message"] == "该引导功能暂时停用"

    @pytest.mark.asyncio
    async def test_no_legacy_quick_session_accessed(self):
        """After the cutover, _fulfill_quick_action must not import or
        access QuickSession, start_session, or advance_active_session."""
        mock_sender = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = True

        mock_db_session = AsyncMock()
        mock_db_session.commit = AsyncMock()
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__.return_value = mock_db_session

        action_config = {
            "flow_id": "small_win_appreciation",
            "task_type": "small_win_appreciation",
            "label": "小赢欣赏",
            "subtitle": "",
            "message_icon": "ud83cudf1f",
        }

        with patch(
            "sales_agent.integrations.dingtalk.quick_entry.get_settings",
            return_value=mock_settings,
        ):
            with patch(
                "sales_agent.core.database.get_session_factory",
                return_value=mock_factory,
            ):
                with patch(
                    "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper",
                ) as mock_mapper_cls:
                    mock_mapper = AsyncMock()
                    mock_mapper.get_or_create_user.return_value = "internal_user_002"
                    mock_mapper_cls.return_value = mock_mapper

                    with patch(
                        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                        new_callable=AsyncMock,
                        return_value="agent_456",
                    ):
                        with patch(
                            "sales_agent.services.online_conversation.invoke_online_turn",
                            new_callable=AsyncMock,
                            return_value={
                                "answer_dict": {
                                    "summary": "今天有什么小进展想分享？",
                                    "sections": [],
                                },
                                "response_kind": "start",
                            },
                        ):
                            with patch(
                                "sales_agent.integrations.dingtalk.quick_entry._get_dingtalk_config",
                            ) as mock_dt_config:
                                dt_config = MagicMock()
                                dt_config.corp_id = "corp_test_001"
                                mock_dt_config.return_value = dt_config

                                # Ensure start_session and QuickSession are NOT imported
                                import sys

                                assert (
                                    "sales_agent.coach.quick_session"
                                    not in sys.modules
                                )

                                from sales_agent.integrations.dingtalk.quick_entry import (
                                    _fulfill_quick_action,
                                )

                                result = await _fulfill_quick_action(
                                    sender=mock_sender,
                                    dingtalk_user_id="ding_user_002",
                                    action="small_win_appreciation",
                                    action_config=action_config,
                                    tenant_id="test_tenant",
                                )

        # Should have sent the summary via online graph
        mock_sender.send_text.assert_awaited_once()
        sent_text = mock_sender.send_text.call_args[0][1]
        assert "今天有什么小进展" in sent_text
        assert result["status"] == "ok"
