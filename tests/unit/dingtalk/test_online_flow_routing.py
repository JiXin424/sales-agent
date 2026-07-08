"""Unit tests: all DingTalk paths route through the Online Conversation Graph.

Asserts:

- Processor passes ``session_user_id=sender_id``, internal ``user_id``,
  ``event_id`` and resolved Agent ID to ``invoke_online_turn``.
- Streaming path uses ``prepare_online_turn`` (shared with standard path).
- Quick-entry ``_fulfill_quick_action`` passes ``entry_action`` to
  ``invoke_online_turn`` and never accesses legacy ``QuickSession``.
- ``resolve_dingtalk_agent_id`` returns the bound Agent when present,
  otherwise the tenant default.
- Standard and streaming paths share the same ``prepare_online_turn``
  contract (Task 6 parity).
- Reset commands route through the Graph with ``reset_requested=True``
  instead of regenerating the conversation ID (Task 6 reset).
"""

from __future__ import annotations

from types import SimpleNamespace
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
# Duplicate delivery is a no-op
# ====================================================================


class TestDuplicateDeliveryNoOp:
    """When ``invoke_online_turn`` returns ``response_kind == "duplicate"``,
    the processor must NOT render or reply — duplicate delivery is silent
    and side-effect free."""

    @pytest.mark.asyncio
    async def test_duplicate_does_not_reply(self):
        reply_fn = AsyncMock()

        mock_db = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.conversation.reset_commands = ["reset", "/reset", "新话题"]
        mock_runtime = MagicMock()
        mock_runtime.tenant_id = "test_tenant"
        mock_config = MagicMock()

        with patch(
            "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
            new_callable=AsyncMock,
            return_value="agent_123",
        ):
            with patch(
                "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
                new_callable=AsyncMock,
                return_value={
                    "response_kind": "duplicate",
                    "thread_id": "online:test_tenant:agent_123:dingtalk:ding_user_001",
                },
            ):
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
                            user_mapper_instance.get_or_create_user.return_value = (
                                "internal_user_001"
                            )
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

                                result = await handle_dingtalk_event(
                                    db=mock_db,
                                    config=mock_config,
                                    settings=mock_settings,
                                    runtime=mock_runtime,
                                    event_id="dup_event_001",
                                    corp_id="corp_001",
                                    sender_id="ding_user_001",
                                    sender_name="Test User",
                                    message_type="text",
                                    text="hello",
                                    dingtalk_conversation_id="conv_001",
                                    reply_fn=reply_fn,
                                )

        # No reply, no render — duplicate delivery must be a no-op.
        from sales_agent.integrations.dingtalk.turn_result import DingTalkTurnResult
        assert isinstance(result, DingTalkTurnResult)
        assert result.response_kind == "duplicate"
        assert result.rendered_text == ""
        reply_fn.assert_not_awaited()
        renderer_instance.render.assert_not_called()


# ====================================================================
# Streaming path routing
# ====================================================================


class TestStreamingPath:
    """The streaming handler must use ``prepare_online_turn`` (shared with
    the standard path) instead of building its own thread/input state."""

    @pytest.mark.asyncio
    async def test_streaming_uses_prepare_online_turn(self):
        """``handle_dingtalk_stream_via_graph`` delegates thread/input/config
        to ``prepare_online_turn`` and calls ``acquire_online_turn_lock``
        before streaming."""
        async def _fake_astream(*args, **kwargs):
            yield ("updates", {"normalize_turn": {"flow_action": "chat"}})
            yield (
                "updates",
                {"chat": {"answer_dict": {"summary": "Streamed reply", "sections": []}}},
            )

        mock_graph = MagicMock()
        mock_graph.astream = _fake_astream

        prepared = SimpleNamespace(
            graph=mock_graph,
            thread_id="online:test_tenant:agent_123:dingtalk:ding_user_001",
            input_state={
                "tenant_id": "test_tenant",
                "agent_id": "agent_123",
                "session_user_id": "ding_user_001",
                "channel": "dingtalk",
                "message": "Test message",
                "event_id": "event_001",
                "guided_flows_enabled": True,
            },
            config={"configurable": {"thread_id": "online:test_tenant:agent_123:dingtalk:ding_user_001"}},
            context={"db": MagicMock(), "chat_model": MagicMock(), "embedding_model": MagicMock(), "now": None},
        )

        mock_card_sender = AsyncMock()
        mock_card_sender.send_markdown_card.return_value = "card_001"

        reply_fn = AsyncMock()

        with patch(
            "sales_agent.integrations.dingtalk.graph_stream.prepare_online_turn",
            new_callable=AsyncMock,
            return_value=prepared,
        ) as mock_prepare:
            with patch(
                "sales_agent.integrations.dingtalk.graph_stream.acquire_online_turn_lock",
                new_callable=AsyncMock,
            ) as mock_lock:
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

        mock_prepare.assert_awaited_once()
        mock_lock.assert_awaited_once()
        assert mock_lock.call_args[0][1] == prepared.thread_id
        assert result["thread_id"] == prepared.thread_id
        assert result["answer_dict"]["summary"] == "Streamed reply"


# ====================================================================
# Standard / Stream parity (Task 6 Step 1)
# ====================================================================


class TestStandardStreamParity:
    """Both ``invoke_online_turn`` and ``handle_dingtalk_stream_via_graph``
    must delegate to ``prepare_online_turn`` for thread_id, input_state,
    config, context, and the Graph — neither builds its own thread/input."""

    @staticmethod
    def _make_prepared():
        async def _fake_astream(*args, **kwargs):
            yield (
                "updates",
                {"chat": {"answer_dict": {"summary": "ok", "sections": []}}},
            )

        graph = MagicMock()
        graph.ainvoke = AsyncMock(
            return_value={"answer_dict": {"summary": "ok"}, "response_kind": "chat"},
        )
        graph.astream = _fake_astream
        return SimpleNamespace(
            graph=graph,
            thread_id="online:t1:a1:dingtalk:du1",
            input_state={
                "tenant_id": "t1", "agent_id": "a1", "user_id": "u1",
                "session_user_id": "du1", "channel": "dingtalk",
                "conversation_id": "c1", "message": "hello", "event_id": "e1",
            },
            config={"configurable": {"thread_id": "online:t1:a1:dingtalk:du1"}},
            context={
                "db": AsyncMock(), "chat_model": MagicMock(),
                "embedding_model": MagicMock(), "now": None,
            },
        )

    @pytest.mark.asyncio
    async def test_invoke_online_turn_uses_prepared_values_and_lock(self):
        prepared = self._make_prepared()
        db = AsyncMock()

        with patch(
            "sales_agent.services.online_conversation.prepare_online_turn",
            new_callable=AsyncMock,
            return_value=prepared,
        ):
            with patch(
                "sales_agent.services.online_conversation.acquire_online_turn_lock",
                new_callable=AsyncMock,
            ) as mock_lock:
                from sales_agent.services.online_conversation import invoke_online_turn

                result = await invoke_online_turn(
                    db=db, tenant_id="t1", agent_id="a1", user_id="u1",
                    session_user_id="du1", channel="dingtalk",
                    conversation_id="c1", message="hello", event_id="e1",
                )

        mock_lock.assert_awaited_once_with(db, prepared.thread_id)
        prepared.graph.ainvoke.assert_awaited_once_with(
            prepared.input_state, prepared.config, context=prepared.context,
        )
        assert result["thread_id"] == prepared.thread_id

    @pytest.mark.asyncio
    async def test_streaming_uses_prepared_values_and_lock(self):
        prepared = self._make_prepared()

        mock_card_sender = AsyncMock()
        mock_card_sender.send_markdown_card.return_value = "card_parity"

        with patch(
            "sales_agent.integrations.dingtalk.graph_stream.prepare_online_turn",
            new_callable=AsyncMock,
            return_value=prepared,
        ):
            with patch(
                "sales_agent.integrations.dingtalk.graph_stream.acquire_online_turn_lock",
                new_callable=AsyncMock,
            ) as mock_lock:
                from sales_agent.integrations.dingtalk.graph_stream import (
                    handle_dingtalk_stream_via_graph,
                )

                await handle_dingtalk_stream_via_graph(
                    tenant_id="t1", user_id="u1", dingtalk_user_id="du1",
                    message="hello", conversation_id="c1", agent_id="a1",
                    event_id="e1", reply_fn=AsyncMock(),
                    card_sender=mock_card_sender, db=MagicMock(),
                    chat_model=MagicMock(), embedding_model=MagicMock(),
                )

        mock_lock.assert_awaited_once()
        assert mock_lock.call_args[0][1] == prepared.thread_id

    @pytest.mark.asyncio
    async def test_neither_path_builds_own_thread_id(self):
        """Neither path may call ``get_online_graph`` directly — the prepared
        turn already owns the graph."""
        prepared = self._make_prepared()
        db = AsyncMock()

        with patch(
            "sales_agent.services.online_conversation.prepare_online_turn",
            new_callable=AsyncMock,
            return_value=prepared,
        ):
            with patch(
                "sales_agent.services.online_conversation.get_online_graph",
            ) as mock_get_graph:
                with patch(
                    "sales_agent.services.online_conversation.acquire_online_turn_lock",
                    new_callable=AsyncMock,
                ):
                    from sales_agent.services.online_conversation import invoke_online_turn

                    await invoke_online_turn(
                        db=db, tenant_id="t1", agent_id="a1", user_id="u1",
                        session_user_id="du1", channel="dingtalk",
                        conversation_id="c1", message="hi", event_id="e1",
                    )

        mock_get_graph.assert_not_called()


# ====================================================================
# Reset via handle_dingtalk_event (Task 6 Step 2)
# ====================================================================


class TestResetViaProcessor:
    """Reset commands must route through the Graph with ``reset_requested=True``
    instead of regenerating the conversation ID locally."""

    @pytest.mark.asyncio
    async def test_pure_reset_passes_reset_requested_and_keeps_conversation_id(self):
        """A pure reset command (no suffix) calls ``invoke_online_turn`` with
        ``reset_requested=True`` and keeps the same conversation_id."""
        reply_fn = AsyncMock()
        mock_db = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.conversation.reset_commands = ["重新开始", "新话题", "/reset"]
        mock_runtime = MagicMock()
        mock_runtime.tenant_id = "test_tenant"
        mock_config = MagicMock()

        reset_result = {
            "response_kind": "reset",
            "answer_dict": {"summary": "已开启新话题。你可以直接说当前要处理的销售问题。", "sections": []},
            "active_flow": None, "flow_stage": None, "flow_payload": {},
            "turn_relation": "new",
            "thread_id": "online:test_tenant:agent_123:dingtalk:ding_user_001",
        }

        with patch(
            "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
            new_callable=AsyncMock, return_value="agent_123",
        ):
            with patch(
                "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
                new_callable=AsyncMock, return_value=reset_result,
            ) as mock_invoke:
                with patch(
                    "sales_agent.integrations.dingtalk.processor.DingTalkMessageRenderer",
                ) as mock_renderer_cls:
                    renderer_instance = MagicMock()
                    renderer_instance.render.return_value = "已开启新话题"
                    mock_renderer_cls.return_value = renderer_instance

                    with patch(
                        "sales_agent.integrations.dingtalk.command_parser.DingTalkCommandParser",
                    ) as mock_parser_cls:
                        parser_instance = MagicMock()
                        parser_instance.parse.return_value = SimpleNamespace(
                            is_reset=True, is_help=False, remaining_message="",
                        )
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
                                mock_conv_mapper_cls.generate_conversation_id.return_value = "conv_001"

                                from sales_agent.integrations.dingtalk.processor import (
                                    handle_dingtalk_event,
                                )

                                result = await handle_dingtalk_event(
                                    db=mock_db, config=mock_config, settings=mock_settings,
                                    runtime=mock_runtime, event_id="reset_ev_001",
                                    corp_id="corp_001", sender_id="ding_user_001",
                                    sender_name="Test User", message_type="text",
                                    text="重新开始", dingtalk_conversation_id="conv_001",
                                    reply_fn=reply_fn,
                                )

        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["reset_requested"] is True
        assert call_kwargs["conversation_id"] == "conv_001"
        reply_fn.assert_awaited_once()
        from sales_agent.integrations.dingtalk.turn_result import DingTalkTurnResult
        assert isinstance(result, DingTalkTurnResult)
        assert result.response_kind == "reset"
        assert result.thread_id == "online:test_tenant:agent_123:dingtalk:ding_user_001"

    @pytest.mark.asyncio
    async def test_reset_with_suffix_routes_suffix_to_graph(self):
        """``重新开始，帮我写开场白`` calls ``invoke_online_turn`` with
        ``reset_requested=True`` and ``message='帮我写开场白'``."""
        reply_fn = AsyncMock()
        mock_db = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.conversation.reset_commands = ["重新开始", "新话题"]
        mock_runtime = MagicMock()
        mock_runtime.tenant_id = "test_tenant"
        mock_config = MagicMock()

        chat_result = {
            "response_kind": "chat",
            "answer_dict": {"summary": "好的，这是你的开场白", "sections": []},
            "active_flow": None, "topic_id": "new_topic_001",
            "turn_relation": "new",
            "thread_id": "online:test_tenant:agent_123:dingtalk:ding_user_001",
        }

        with patch(
            "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
            new_callable=AsyncMock, return_value="agent_123",
        ):
            with patch(
                "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
                new_callable=AsyncMock, return_value=chat_result,
            ) as mock_invoke:
                with patch(
                    "sales_agent.integrations.dingtalk.processor.DingTalkMessageRenderer",
                ) as mock_renderer_cls:
                    renderer_instance = MagicMock()
                    renderer_instance.render.return_value = "Rendered opening"
                    mock_renderer_cls.return_value = renderer_instance

                    with patch(
                        "sales_agent.integrations.dingtalk.command_parser.DingTalkCommandParser",
                    ) as mock_parser_cls:
                        parser_instance = MagicMock()
                        parser_instance.parse.return_value = SimpleNamespace(
                            is_reset=True, is_help=False,
                            remaining_message="帮我写开场白",
                        )
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
                                mock_conv_mapper_cls.generate_conversation_id.return_value = "conv_001"
                                from sales_agent.integrations.dingtalk.processor import (
                                    handle_dingtalk_event,
                                )

                                await handle_dingtalk_event(
                                    db=mock_db, config=mock_config, settings=mock_settings,
                                    runtime=mock_runtime, event_id="reset_ev_002",
                                    corp_id="corp_001", sender_id="ding_user_001",
                                    sender_name="Test User", message_type="text",
                                    text="重新开始，帮我写开场白",
                                    dingtalk_conversation_id="conv_001",
                                    reply_fn=reply_fn,
                                )

        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["reset_requested"] is True
        assert call_kwargs["message"] == "帮我写开场白"
        assert call_kwargs["conversation_id"] == "conv_001"

    @pytest.mark.asyncio
    async def test_reset_failure_reraises_after_error_reply(self):
        """On Graph/persistence failure the processor sends a best-effort error
        reply AND re-raises so the worker rolls back."""
        reply_fn = AsyncMock()
        mock_db = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.conversation.reset_commands = ["重新开始"]
        mock_runtime = MagicMock()
        mock_runtime.tenant_id = "test_tenant"
        mock_config = MagicMock()

        with patch(
            "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
            new_callable=AsyncMock, return_value="agent_123",
        ):
            with patch(
                "sales_agent.integrations.dingtalk.processor.invoke_online_turn",
                new_callable=AsyncMock,
                side_effect=RuntimeError("graph blew up"),
            ):
                with patch(
                    "sales_agent.integrations.dingtalk.processor.DingTalkMessageRenderer",
                ) as mock_renderer_cls:
                    renderer_instance = MagicMock()
                    renderer_instance.render_error.return_value = "Service error"
                    mock_renderer_cls.return_value = renderer_instance

                    with patch(
                        "sales_agent.integrations.dingtalk.command_parser.DingTalkCommandParser",
                    ) as mock_parser_cls:
                        parser_instance = MagicMock()
                        parser_instance.parse.return_value = SimpleNamespace(
                            is_reset=False, is_help=False, remaining_message="hello",
                        )
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

                                with pytest.raises(RuntimeError, match="graph blew up"):
                                    await handle_dingtalk_event(
                                        db=mock_db, config=mock_config,
                                        settings=mock_settings, runtime=mock_runtime,
                                        event_id="fail_ev", corp_id="corp_001",
                                        sender_id="ding_user_001", sender_name="Test",
                                        message_type="text", text="hello",
                                        dingtalk_conversation_id="conv_001",
                                        reply_fn=reply_fn,
                                    )

        reply_fn.assert_awaited()


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
