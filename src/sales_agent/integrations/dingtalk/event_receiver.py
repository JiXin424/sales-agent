"""钉钉事件接收和标准化。

将钉钉回调原始事件转换为内部标准事件模型。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sales_agent.core.exceptions import DingTalkSignatureError
from sales_agent.integrations.dingtalk.signature import DingTalkSignatureVerifier

logger = logging.getLogger(__name__)


@dataclass
class NormalizedDingTalkEvent:
    """标准化钉钉事件。"""

    event_id: str
    corp_id: str
    sender_id: str
    sender_name: str
    conversation_type: str  # "single" / "group"
    conversation_id: str  # 钉钉的 conversation ID
    message_id: str
    message_type: str  # "text", "picture", "richText", "voice", "file", "unknown"
    text: str | None
    media_download_codes: list[str] = field(default_factory=list)
    raw_event: dict[str, Any] = field(default_factory=dict)


class DingTalkEventReceiver:
    """钉钉事件接收器：解析和标准化回调事件。"""

    def __init__(self, verifier: DingTalkSignatureVerifier):
        self._verifier = verifier

    def parse_and_verify(
        self,
        timestamp: str,
        sign: str,
        body: bytes,
    ) -> NormalizedDingTalkEvent:
        """解析并验证钉钉回调事件。

        Args:
            timestamp: 请求头中的 timestamp
            sign: 请求头中的 sign
            body: 原始请求体

        Returns:
            标准化的事件

        Raises:
            DingTalkSignatureError: 签名验证失败
            ValueError: 事件格式无效
        """
        # 1. 验签
        if not self._verifier.verify(timestamp, sign):
            raise DingTalkSignatureError(
                detail=f"Signature verification failed for timestamp={timestamp}"
            )

        # 2. 解析 body
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON body: {e}")

        # 3. 尝试解密（如果启用了加密回调）
        event_data = self._maybe_decrypt(payload)

        # 4. 标准化
        return self._normalize(event_data)

    def _maybe_decrypt(self, payload: dict) -> dict:
        """如果启用了加密回调，解密事件体。"""
        encrypt = payload.get("encrypt")
        if encrypt and self._verifier._aes_key:
            try:
                decrypted_json = self._verifier.decrypt(encrypt)
                return json.loads(decrypted_json)
            except Exception as e:
                logger.warning("Failed to decrypt DingTalk event: %s", e)
                # 降级：直接使用原始 payload
                return payload
        return payload

    def _normalize(self, event_data: dict[str, Any]) -> NormalizedDingTalkEvent:
        """将钉钉事件数据标准化为内部事件模型。

        钉钉单聊机器人事件的字段（以实际开放平台文档为准）：
        - msgtype: 消息类型
        - text.content: 文本内容
        - senderStaffId / senderId: 发送者 ID
        - senderNick: 发送者昵称
        - conversationId: 会话 ID
        - msgId: 消息 ID
        - conversationType: "1"=单聊, "2"=群聊
        - chatbotCorpId / corpId: 企业 ID
        """
        # 提取消息类型
        msg_type = event_data.get("msgtype", "unknown")

        # 提取文本内容和媒体 downloadCode
        text = self._extract_text(event_data, msg_type)
        media_download_codes = self._extract_download_codes(event_data)

        # 提取发送者信息
        sender_id = (
            event_data.get("senderStaffId")
            or event_data.get("senderId")
            or event_data.get("staffId")
            or ""
        )
        sender_name = event_data.get("senderNick") or event_data.get("senderName") or ""

        # 提取会话信息
        conversation_id = event_data.get("conversationId") or ""
        conversation_type_raw = str(event_data.get("conversationType", "1"))
        conversation_type = "single" if conversation_type_raw == "1" else "group"

        # 消息 ID
        message_id = event_data.get("msgId") or event_data.get("messageId") or ""

        # 企业 ID
        corp_id = (
            event_data.get("chatbotCorpId")
            or event_data.get("corpId")
            or event_data.get("corp_id")
            or ""
        )

        # 生成事件 ID（用于去重）
        event_id = self._generate_event_id(corp_id, sender_id, message_id, text)

        # 脱敏 raw_event
        raw_event = self._verifier.sanitize_raw_event(event_data)

        return NormalizedDingTalkEvent(
            event_id=event_id,
            corp_id=corp_id,
            sender_id=sender_id,
            sender_name=sender_name,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            message_id=message_id,
            message_type=msg_type,
            text=text,
            media_download_codes=media_download_codes,
            raw_event=raw_event,
        )

    def _extract_text(self, event_data: dict[str, Any], msg_type: str) -> str | None:
        if msg_type == "text":
            text_obj = event_data.get("text", {})
            if isinstance(text_obj, dict):
                return text_obj.get("content", "").strip()
            if isinstance(text_obj, str):
                return text_obj.strip()
        if msg_type == "richText":
            text, _ = _extract_rich_text_parts(event_data.get("content"))
            return text or None
        return None

    def _extract_download_codes(self, event_data: dict[str, Any]) -> list[str]:
        codes: list[str] = []
        if event_data.get("msgtype") == "richText":
            _, rich_codes = _extract_rich_text_parts(event_data.get("content"))
            codes.extend(rich_codes)
        _walk_download_codes(event_data, codes)
        return list(dict.fromkeys(codes))

    @staticmethod
    def _generate_event_id(
        corp_id: str, sender_id: str, message_id: str, text: str | None
    ) -> str:
        """生成事件唯一 ID。

        优先使用钉钉提供的 message_id，
        否则基于 corp_id + sender_id + text 生成哈希。
        """
        if message_id:
            return f"ding_{message_id}"

        # 降级：哈希生成
        raw = f"{corp_id}:{sender_id}:{text or ''}"
        hash_val = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"ding_hash_{hash_val}"


def _walk_download_codes(value: Any, codes: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"downloadCode", "download_code"} and isinstance(child, str) and child:
                codes.append(child)
            else:
                _walk_download_codes(child, codes)
    elif isinstance(value, list):
        for item in value:
            _walk_download_codes(item, codes)


def _extract_rich_text_parts(content: Any) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    codes: list[str] = []
    items = []
    if isinstance(content, dict):
        items = content.get("richText") or content.get("richTextList") or content.get("items") or []
    elif isinstance(content, list):
        items = content
    for item in items:
        if not isinstance(item, dict):
            continue
        txt = item.get("text")
        if isinstance(txt, str) and txt.strip():
            text_parts.append(txt.strip())
        code = item.get("downloadCode") or item.get("download_code")
        if isinstance(code, str) and code:
            codes.append(code)
    return "\n".join(text_parts).strip(), codes
