"""快捷入口 OAuth2 回调辅助函数单测（PC 浏览器场景） + OAuth2 路由验证。

验证 OAuth2 路径（dingtalk_oauth2_callback）中 _fulfill_quick_action
不再使用 legacy start_session/QuickSession。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse

from sales_agent.integrations.dingtalk.quick_entry import (
    _oauth_result_page,
    _parse_oauth_action,
)


class TestParseOauthAction:
    def test_action_only(self):
        # tenant_id 已在 URL path 段（/t/{tenant}/oauth2-callback），state 只传 action
        assert _parse_oauth_action("pre_visit_prepare") == "pre_visit_prepare"

    def test_sales_block(self):
        assert _parse_oauth_action("sales_block_breakthrough") == "sales_block_breakthrough"

    def test_legacy_state_with_tenant(self):
        # 兼容旧的 action:tenant_id 格式（取冒号前，tenant 由 path 段携带）
        assert _parse_oauth_action("pre_visit_prepare:taishan") == "pre_visit_prepare"

    def test_empty(self):
        assert _parse_oauth_action("") == ""


class TestOAuthResultPage:
    def test_returns_html(self):
        resp = _oauth_result_page(True, "已发送")
        assert isinstance(resp, HTMLResponse)
        assert "已发送" in resp.body.decode("utf-8")

    def test_escapes_message(self):
        # 异常文本里的 HTML 不应原样渲染（防注入）
        resp = _oauth_result_page(False, "<script>x</script>")
        body = resp.body.decode("utf-8")
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_ok_vs_err_color(self):
        ok_body = _oauth_result_page(True, "x").body.decode()
        err_body = _oauth_result_page(False, "x").body.decode()
        assert "#52c41a" in ok_body      # 成功绿
        assert "#ff4d4f" in err_body     # 失败红


class TestOAuth2Routing:
    """OAuth2 回调路由验证（不接触 legacy quick_sessions）。"""

    @pytest.mark.asyncio
    async def test_oauth2_callback_routes_through_online_graph(self):
        """模拟 dingtalk_oauth2_callback 的 _fulfill_quick_action 调用
        验证 invoke_online_turn 被正确使用且 QuickSession 未引入。"""
        mock_sender = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.guided_flows.enabled = True

        mock_db_session = AsyncMock()
        mock_db_session.commit = AsyncMock()
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__.return_value = mock_db_session

        from sales_agent.integrations.dingtalk.quick_entry import (
            _QUICK_ENTRY_ACTIONS,
        )

        action_config = _QUICK_ENTRY_ACTIONS["small_win_appreciation"]
        mock_dt_config = MagicMock()
        mock_dt_config.corp_id = "corp_test"

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
                    mock_mapper.get_or_create_user.return_value = "internal_user_oauth2"
                    mock_mapper_cls.return_value = mock_mapper

                    with patch(
                        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                        new_callable=AsyncMock,
                        return_value="agent_oauth2",
                    ):
                        with patch(
                            "sales_agent.services.online_conversation.invoke_online_turn",
                            new_callable=AsyncMock,
                            return_value={
                                "answer_dict": {
                                    "summary": "今天有什么小进展？",
                                    "sections": [],
                                },
                                "response_kind": "start",
                            },
                        ) as mock_invoke:
                            with patch(
                                "sales_agent.integrations.dingtalk.quick_entry._get_dingtalk_config",
                                return_value=mock_dt_config,
                            ):
                                from sales_agent.integrations.dingtalk.quick_entry import (
                                    _fulfill_quick_action,
                                )

                                result = await _fulfill_quick_action(
                                    sender=mock_sender,
                                    dingtalk_user_id="ding_user_oauth2",
                                    action="small_win_appreciation",
                                    action_config=action_config,
                                    tenant_id="test_tenant",
                                )

        mock_invoke.assert_awaited_once()
        kwargs = mock_invoke.call_args[1]
        assert kwargs["entry_action"] == "small_win_appreciation"
        assert kwargs["message"] == ""
        assert kwargs["channel"] == "dingtalk"
        assert result["status"] == "ok"

        import sys
        assert "sales_agent.coach.quick_session" not in sys.modules
