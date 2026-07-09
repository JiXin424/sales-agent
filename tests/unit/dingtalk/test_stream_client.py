"""DingTalk Stream client: streaming gate + media→text resolution.

Covers the behaviour that lets voice/image messages take the streaming card
path (previously hard-gated to text only).
"""

import pytest

from sales_agent.integrations.dingtalk import graph_stream, stream_client
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.media_adapter import DingTalkMediaAdapter


class _FakeMediaAdapter(DingTalkMediaAdapter):
    """Adapter returning canned text without touching the network."""

    def __init__(self, *, transcript="识别内容", raise_exc=None):
        super().__init__(DingTalkConfig(enabled=True, media_enabled=True), settings=None)
        self._transcript = transcript
        self._raise_exc = raise_exc
        self.closed = False

    async def to_agent_text(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return self._transcript

    async def close(self):
        self.closed = True


class _FakeCardSender:
    def __init__(self):
        self.created = []      # list of (dingtalk_user_id, title, markdown_text)
        self.finalized = []    # list of (out_track_id, content)

    async def send_markdown_card(self, *, dingtalk_user_id, title, markdown_text):
        out_track_id = f"card-{len(self.created) + 1}"
        self.created.append((dingtalk_user_id, title, markdown_text))
        return out_track_id

    async def streaming_finalize(self, out_track_id, content, **kwargs):
        self.finalized.append((out_track_id, content))


# ---------------------------------------------------------------------------
# 改动 1: streaming gate
# ---------------------------------------------------------------------------

def test_gate_text_streams_when_enabled():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True)
    assert stream_client._should_stream(cfg, "text", "你好") is True


def test_gate_text_blocked_when_streaming_off():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=False, media_enabled=True)
    assert stream_client._should_stream(cfg, "text", "你好") is False


def test_gate_fast_command_does_not_stream():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True)
    assert stream_client._should_stream(cfg, "text", "帮助") is False
    assert stream_client._should_stream(cfg, "text", "/reset") is False
    assert stream_client._should_stream(cfg, "text", "  新话题  ") is False


def test_gate_media_streams_when_media_enabled():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True)
    assert stream_client._should_stream(cfg, "voice", "") is True
    assert stream_client._should_stream(cfg, "picture", "") is True
    assert stream_client._should_stream(cfg, "audio", "") is True
    assert stream_client._should_stream(cfg, "richText", "") is True


def test_gate_media_blocked_when_media_disabled():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=False)
    assert stream_client._should_stream(cfg, "voice", "") is False


def test_gate_media_blocked_when_streaming_off():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=False, media_enabled=True)
    assert stream_client._should_stream(cfg, "picture", "") is False


def test_gate_unsupported_type_never_streams():
    cfg = DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True)
    assert stream_client._should_stream(cfg, "video", "") is False
    assert stream_client._should_stream(cfg, "fallback", "") is False


# ---------------------------------------------------------------------------
# 改动 3: media → text resolution with recognition card
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_text_passthrough_creates_no_card():
    card = _FakeCardSender()
    msg, card_id = await stream_client._resolve_streaming_message(
        DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True),
        settings=None,
        card_sender=card,
        message_type="text",
        text_content="你好",
        media_download_codes=[],
        raw_event={},
        dingtalk_user_id="u1",
        reply_fn=None,
    )
    assert msg == "你好"
    assert card_id is None
    assert card.created == []  # text never opens a recognition card


@pytest.mark.asyncio
async def test_resolve_media_creates_recognition_card_and_transcribes(monkeypatch):
    card = _FakeCardSender()
    fake = _FakeMediaAdapter(transcript="[用户发送了一段语音，转写如下：你好]")
    monkeypatch.setattr(stream_client, "DingTalkMediaAdapter", lambda c, s: fake)

    msg, card_id = await stream_client._resolve_streaming_message(
        DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True),
        settings=None,
        card_sender=card,
        message_type="voice",
        text_content="",
        media_download_codes=["c1"],
        raw_event={},
        dingtalk_user_id="u1",
        reply_fn=None,
    )
    assert msg == "[用户发送了一段语音，转写如下：你好]"
    assert card_id == "card-1"
    assert card.created == [("u1", "正在识别...", "正在识别你的语音/图片…")]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_resolve_media_failure_finalizes_card_with_friendly_error(monkeypatch):
    card = _FakeCardSender()
    fake = _FakeMediaAdapter(raise_exc=ValueError("ASR failed"))
    monkeypatch.setattr(stream_client, "DingTalkMediaAdapter", lambda c, s: fake)

    msg, card_id = await stream_client._resolve_streaming_message(
        DingTalkConfig(enabled=True, streaming_enabled=True, media_enabled=True),
        settings=None,
        card_sender=card,
        message_type="picture",
        text_content="",
        media_download_codes=["c1"],
        raw_event={},
        dingtalk_user_id="u1",
        reply_fn=None,
    )
    assert msg is None            # signals: do not continue to graph
    assert card_id == "card-1"    # recognition card was opened
    assert len(card.finalized) == 1
    assert card.finalized[0][0] == "card-1"
    assert "无法识别" in card.finalized[0][1]
    assert fake.closed is True


# ---------------------------------------------------------------------------
# 改动 4: handle_dingtalk_stream_via_graph reuses a provided card_id
# ---------------------------------------------------------------------------

class _FakeGraph:
    def __init__(self):
        self.yielded = False

    async def astream(self, *args, **kwargs):
        # One "updates" chunk carrying the final answer_dict (no token stream).
        yield ("updates", {"generation": {"answer_dict": {
            "summary": "这是答案", "sections": [], "sources": [],
        }}})


class _FakePrepared:
    def __init__(self):
        self.thread_id = "thread-1"
        self.input_state = {}
        self.config = {}
        self.context = {}
        self.graph = _FakeGraph()


@pytest.mark.asyncio
async def test_graph_stream_reuses_provided_card_id(monkeypatch):
    prepared = _FakePrepared()
    monkeypatch.setattr(graph_stream, "prepare_online_turn", lambda **kw: _async_value(prepared))
    monkeypatch.setattr(graph_stream, "acquire_online_turn_lock", lambda db, tid: _async_value(None))

    card = _FakeCardSender()
    await graph_stream.handle_dingtalk_stream_via_graph(
        tenant_id="t",
        user_id="u",
        dingtalk_user_id="du",
        message="你好",
        conversation_id="c",
        agent_id=None,
        reply_fn=None,
        card_sender=card,
        db=None,
        chat_model=None,
        card_id="existing-card-1",
    )
    assert card.created == []                       # did NOT open a new card
    assert card.finalized[0][0] == "existing-card-1"  # finalized the reused card


@pytest.mark.asyncio
async def test_graph_stream_creates_card_when_none_provided(monkeypatch):
    prepared = _FakePrepared()
    monkeypatch.setattr(graph_stream, "prepare_online_turn", lambda **kw: _async_value(prepared))
    monkeypatch.setattr(graph_stream, "acquire_online_turn_lock", lambda db, tid: _async_value(None))

    card = _FakeCardSender()
    await graph_stream.handle_dingtalk_stream_via_graph(
        tenant_id="t",
        user_id="u",
        dingtalk_user_id="du",
        message="你好",
        conversation_id="c",
        agent_id=None,
        reply_fn=None,
        card_sender=card,
        db=None,
        chat_model=None,
    )
    assert len(card.created) == 1                   # opened the default 分析中... card
    assert card.created[0][1] == "分析中..."
    assert card.finalized[0][0] == "card-1"


async def _async_value(value):
    return value
