"""钉钉会话映射单元测试。"""

from sales_agent.integrations.dingtalk.conversation_mapper import DingTalkConversationMapper


class TestDingTalkConversationMapper:

    def test_generates_expected_format(self):
        result = DingTalkConversationMapper.generate_conversation_id(
            "taishan", "ding_user_001",
        )
        assert result == "dingtalk_single:taishan:ding_user_001"

    def test_idempotent(self):
        r1 = DingTalkConversationMapper.generate_conversation_id("t1", "u1")
        r2 = DingTalkConversationMapper.generate_conversation_id("t1", "u1")
        assert r1 == r2

    def test_different_users_different_ids(self):
        r1 = DingTalkConversationMapper.generate_conversation_id("t1", "u1")
        r2 = DingTalkConversationMapper.generate_conversation_id("t1", "u2")
        assert r1 != r2

    def test_different_tenants_different_ids(self):
        r1 = DingTalkConversationMapper.generate_conversation_id("t1", "u1")
        r2 = DingTalkConversationMapper.generate_conversation_id("t2", "u1")
        assert r1 != r2
