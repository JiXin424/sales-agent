
import json

from tests.unit.dingtalk.test_event_receiver import _compute_sign, _make_config, _make_verifier
from sales_agent.integrations.dingtalk.event_receiver import DingTalkEventReceiver


def test_parse_picture_download_code():
    config = _make_config()
    receiver = DingTalkEventReceiver(_make_verifier(config))
    timestamp = "1700000000000"
    sign = _compute_sign(timestamp, config.app_secret)

    body = json.dumps({
        "msgtype": "picture",
        "content": {"downloadCode": "img-code-1"},
        "senderStaffId": "user_003",
        "conversationType": "1",
        "chatbotCorpId": "corp_test_001",
    }).encode("utf-8")

    event = receiver.parse_and_verify(timestamp, sign, body)

    assert event.message_type == "picture"
    assert event.media_download_codes == ["img-code-1"]


def test_parse_rich_text_keeps_text_and_image_codes():
    config = _make_config()
    receiver = DingTalkEventReceiver(_make_verifier(config))
    timestamp = "1700000000000"
    sign = _compute_sign(timestamp, config.app_secret)

    body = json.dumps({
        "msgtype": "richText",
        "content": {
            "richText": [
                {"text": "帮我看这张截图"},
                {"downloadCode": "img-code-1"},
            ]
        },
        "senderStaffId": "user_003",
        "conversationType": "1",
        "chatbotCorpId": "corp_test_001",
    }).encode("utf-8")

    event = receiver.parse_and_verify(timestamp, sign, body)

    assert event.message_type == "richText"
    assert event.text == "帮我看这张截图"
    assert event.media_download_codes == ["img-code-1"]


def test_parse_voice_download_code_from_content():
    config = _make_config()
    receiver = DingTalkEventReceiver(_make_verifier(config))
    timestamp = "1700000000000"
    sign = _compute_sign(timestamp, config.app_secret)

    body = json.dumps({
        "msgtype": "voice",
        "content": {"downloadCode": "voice-code-1"},
        "senderStaffId": "user_003",
        "conversationType": "1",
        "chatbotCorpId": "corp_test_001",
    }).encode("utf-8")

    event = receiver.parse_and_verify(timestamp, sign, body)

    assert event.message_type == "voice"
    assert event.media_download_codes == ["voice-code-1"]
