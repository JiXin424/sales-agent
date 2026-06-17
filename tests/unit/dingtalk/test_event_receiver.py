"""钉钉事件接收器单元测试。"""

import json

import pytest

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.event_receiver import (
    DingTalkEventReceiver,
    NormalizedDingTalkEvent,
)
from sales_agent.integrations.dingtalk.signature import DingTalkSignatureVerifier


def _make_config(**kwargs) -> DingTalkConfig:
    defaults = {
        "app_secret": "test_secret",
        "corp_id": "corp_test_001",
    }
    defaults.update(kwargs)
    return DingTalkConfig(enabled=True, **defaults)


def _make_verifier(config: DingTalkConfig) -> DingTalkSignatureVerifier:
    return DingTalkSignatureVerifier(config)


def _compute_sign(timestamp: str, app_secret: str) -> str:
    import base64, hashlib, hmac
    string_to_sign = f"{timestamp}\n{app_secret}"
    hmac_code = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


class TestEventReceiver:

    def test_parse_text_single_chat(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        body = json.dumps({
            "msgtype": "text",
            "text": {"content": "客户说太贵了怎么回？"},
            "senderStaffId": "ding_user_001",
            "senderNick": "张三",
            "conversationId": "ding_conv_001",
            "msgId": "ding_msg_001",
            "conversationType": "1",
            "chatbotCorpId": "corp_test_001",
        }).encode("utf-8")

        event = receiver.parse_and_verify(timestamp, sign, body)

        assert isinstance(event, NormalizedDingTalkEvent)
        assert event.corp_id == "corp_test_001"
        assert event.sender_id == "ding_user_001"
        assert event.sender_name == "张三"
        assert event.conversation_type == "single"
        assert event.message_type == "text"
        assert event.text == "客户说太贵了怎么回？"
        assert event.message_id == "ding_msg_001"
        assert event.event_id == "ding_ding_msg_001"

    def test_parse_group_chat(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        body = json.dumps({
            "msgtype": "text",
            "text": {"content": "hello"},
            "senderStaffId": "user_002",
            "conversationType": "2",
            "chatbotCorpId": "corp_test_001",
        }).encode("utf-8")

        event = receiver.parse_and_verify(timestamp, sign, body)
        assert event.conversation_type == "group"

    def test_parse_non_text_message(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        body = json.dumps({
            "msgtype": "picture",
            "senderStaffId": "user_003",
            "conversationType": "1",
            "chatbotCorpId": "corp_test_001",
        }).encode("utf-8")

        event = receiver.parse_and_verify(timestamp, sign, body)
        assert event.message_type == "picture"
        assert event.text is None

    def test_parse_invalid_json_raises(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        with pytest.raises(ValueError, match="Invalid JSON"):
            receiver.parse_and_verify(timestamp, sign, b"not json")

    def test_parse_bad_signature_raises(self):
        from sales_agent.core.exceptions import DingTalkSignatureError

        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        body = json.dumps({"msgtype": "text"}).encode("utf-8")

        with pytest.raises(DingTalkSignatureError):
            receiver.parse_and_verify("1700000000000", "bad_sign", body)

    def test_raw_event_sanitized(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        body = json.dumps({
            "msgtype": "text",
            "text": {"content": "hi"},
            "senderStaffId": "user_004",
            "conversationType": "1",
            "chatbotCorpId": "corp_test_001",
            "appSecret": "leaked_secret",
        }).encode("utf-8")

        event = receiver.parse_and_verify(timestamp, sign, body)
        assert event.raw_event.get("appSecret") == "[redacted]"

    def test_event_id_from_hash_when_no_message_id(self):
        config = _make_config()
        verifier = _make_verifier(config)
        receiver = DingTalkEventReceiver(verifier)

        timestamp = "1700000000000"
        sign = _compute_sign(timestamp, config.app_secret)

        body = json.dumps({
            "msgtype": "text",
            "text": {"content": "test"},
            "senderStaffId": "user_005",
            "conversationType": "1",
            "chatbotCorpId": "corp_test_001",
        }).encode("utf-8")

        event = receiver.parse_and_verify(timestamp, sign, body)
        assert event.event_id.startswith("ding_hash_")
