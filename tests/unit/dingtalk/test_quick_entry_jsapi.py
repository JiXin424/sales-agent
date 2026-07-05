"""快捷入口 dd.config 签名（sign_jsapi）单元测试 + JSAPI 路由验证。

背景：PC 钉钉调用 requestAuthCode 前必须 dd.config 鉴权，签名串为
``jsapi_ticket=..&noncestr=..&timestamp=..&url=..``（键按字典序、
noncestr/timestamp 全小写），取 SHA1 小写十六进制。签错任一字段名都会导致
PC 端回调不触发、页面卡死。

验证 JSAPI 路径（dingtalk_quick_whoami）中 _fulfill_quick_action
不再使用 legacy start_session/QuickSession。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sales_agent.integrations.dingtalk.quick_entry import sign_jsapi


class TestSignJsapi:
    def test_known_vector(self):
        # 与实现同源手算的固定向量（独立用 chr(38) 拼 & 避免 &times 实体污染），
        # 锁死算法（含 query string 的 url）。
        sig = sign_jsapi(
            jsapi_ticket="sTgptestTicketXYZ123",
            noncestr="abc123def456",
            timestamp="1718600000",
            url="https://aijiaolian.com.cn/integrations/dingtalk/quick?tenant_id=taishan",
        )
        assert sig == "a593403d71d6c7ca77fc970e40216dca23ef6a63"

    def test_lowercase_hex_sha1_length(self):
        sig = sign_jsapi("t", "n", "1", "https://x.example/y")
        assert len(sig) == 40
        assert sig == sig.lower()

    def test_url_with_query_and_fragment(self):
        # 真实页面 url 含 action/tenant_id；签名必须用完整 url（含 query）。
        a = sign_jsapi(
            "ticket", "nonce", "1700000000",
            "https://h.example/quick?action=pre_visit_prepare&tenant_id=taishan",
        )
        b = sign_jsapi("ticket", "nonce", "1700000000", "https://h.example/quick")
        assert a != b  # query 不同 → 签名不同

    def test_ordering_sensitive(self):
        # 钉钉历史坑：dd.config 传 nonceStr/timeStamp（驼峰），但签名串里
        # 必须是 noncestr/timestamp（全小写），且分隔符是字面 &（不是乘号 ×）。
        # 用 chr(38) 拼 & 构造期望值，避免源码里 &times 被实体解码污染（曾经
        # 让 impl 的 &timestamp 被误转成 ×tamp，签名全错、dd.config 静默失败）。
        import hashlib
        sig = sign_jsapi("t", "n", "1", "u")
        raw = (
            "jsapi_ticket=t" + chr(38) + "noncestr=n" + chr(38)
            + "timestamp=1" + chr(38) + "url=u"
        )
        expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        assert sig == expected

    def test_not_mangled_to_multiplication_sign(self):
        # 回归：&timestamp 绝不能被转成乘号 ×（c3 97）。乘号版与本实现必不同。
        import hashlib
        mangled = hashlib.sha1(
            ("jsapi_ticket=t" + chr(38) + "noncestr=n"
             + "×tamp=1" + chr(38) + "url=u").encode("utf-8")
        ).hexdigest()
        assert sign_jsapi("t", "n", "1", "u") != mangled


class TestJsapiRouting:
    """JSAPI whoami 路径验证在线图路由（不接触 legacy quick_sessions）。"""

    @pytest.mark.asyncio
    async def test_whoami_routes_through_online_graph(self):
        """模拟 dingtalk_quick_whoami 的 _fulfill_quick_action 调用
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

        action_config = _QUICK_ENTRY_ACTIONS["post_visit_review"]
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
                    mock_mapper.get_or_create_user.return_value = "internal_user_jsapi"
                    mock_mapper_cls.return_value = mock_mapper

                    with patch(
                        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
                        new_callable=AsyncMock,
                        return_value="agent_jsapi",
                    ):
                        with patch(
                            "sales_agent.services.online_conversation.invoke_online_turn",
                            new_callable=AsyncMock,
                            return_value={
                                "answer_dict": {
                                    "summary": "访后复盘问题",
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
                                    dingtalk_user_id="ding_user_jsapi",
                                    action="post_visit_review",
                                    action_config=action_config,
                                    tenant_id="test_tenant",
                                )

        mock_invoke.assert_awaited_once()
        kwargs = mock_invoke.call_args[1]
        assert kwargs["entry_action"] == "post_visit_review"
        assert kwargs["message"] == ""
        assert kwargs["channel"] == "dingtalk"
        assert result["status"] == "ok"

        import sys
        assert "sales_agent.coach.quick_session" not in sys.modules
