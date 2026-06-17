
import pytest

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.media_adapter import DingTalkMediaAdapter


class FakeAdapter(DingTalkMediaAdapter):
    def __init__(self):
        super().__init__(DingTalkConfig(enabled=True, media_enabled=True), settings=None)
        self.downloaded = []

    async def _download_media_bytes(self, download_code: str):
        self.downloaded.append(download_code)
        return b"media-bytes", "image/png"

    async def _describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        assert image_bytes == b"media-bytes"
        assert mime_type == "image/png"
        return "图片里是一张客户聊天截图，客户反馈预算不足。"

    async def _transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        assert audio_bytes == b"media-bytes"
        return "客户说价格太高，想下周再聊。"


@pytest.mark.asyncio
async def test_picture_message_is_converted_to_visual_context_text():
    adapter = FakeAdapter()

    text = await adapter.to_agent_text(
        message_type="picture",
        text=None,
        download_codes=["img-code-1"],
        raw_event={},
    )

    assert adapter.downloaded == ["img-code-1"]
    assert "用户发送了图片" in text
    assert "客户反馈预算不足" in text


@pytest.mark.asyncio
async def test_rich_text_keeps_text_and_adds_image_understanding():
    adapter = FakeAdapter()

    text = await adapter.to_agent_text(
        message_type="richText",
        text="帮我看看这个客户截图怎么回",
        download_codes=["img-code-1"],
        raw_event={},
    )

    assert "帮我看看这个客户截图怎么回" in text
    assert "图片里是一张客户聊天截图" in text


@pytest.mark.asyncio
async def test_voice_message_is_converted_to_transcript_text():
    adapter = FakeAdapter()

    text = await adapter.to_agent_text(
        message_type="voice",
        text=None,
        download_codes=["voice-code-1"],
        raw_event={},
    )

    assert adapter.downloaded == ["voice-code-1"]
    assert "用户发送了一段语音" in text
    assert "客户说价格太高" in text


@pytest.mark.asyncio
async def test_voice_uses_dingtalk_platform_recognition_text_before_asr():
    adapter = FakeAdapter()

    text = await adapter.to_agent_text(
        message_type="audio",
        text=None,
        download_codes=["voice-code-1"],
        raw_event={"content": {"recognition": "客户说预算不够，想月底再定。"}},
    )

    assert adapter.downloaded == []
    assert "用户发送了一段语音" in text
    assert "客户说预算不够" in text


@pytest.mark.asyncio
async def test_media_disabled_returns_clear_error():
    adapter = DingTalkMediaAdapter(
        DingTalkConfig(enabled=True, media_enabled=False),
        settings=None,
    )

    with pytest.raises(ValueError, match="Media message support is disabled"):
        await adapter.to_agent_text(
            message_type="picture",
            text=None,
            download_codes=["img-code-1"],
            raw_event={},
        )
