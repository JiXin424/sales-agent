"""钉钉会话映射 — 生成稳定的内部 conversation_id。"""


class DingTalkConversationMapper:
    """钉钉会话 ID 映射器。"""

    @staticmethod
    def generate_conversation_id(tenant_id: str, dingtalk_user_id: str) -> str:
        """生成稳定的内部 conversation_id。

        格式：dingtalk_single:{tenant_id}:{dingtalk_user_id}

        Args:
            tenant_id: 内部租户 ID
            dingtalk_user_id: 钉钉用户 ID

        Returns:
            稳定的 conversation_id 字符串
        """
        return f"dingtalk_single:{tenant_id}:{dingtalk_user_id}"
