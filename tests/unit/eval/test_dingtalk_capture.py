"""Unit tests for PublicReplyCapture — the staging reply_fn gate (Spec 4 §3.4).

The staging runner must capture ONLY public outbound delivery and drop any
internal/audit text so it is never scored as user-facing output.
"""
from __future__ import annotations

import pytest

from eval.memory_eval.dingtalk_capture import PublicReplyCapture


@pytest.mark.asyncio
async def test_capture_keeps_public_drops_internal():
    cap = PublicReplyCapture()
    await cap.reply("这是给用户的公开回复")
    await cap.reply("[internal] 审计日志不外发")
    await cap.reply("[audit] memory write event")
    await cap.reply("[memory-internal] profile update")
    assert cap.public_replies == ["这是给用户的公开回复"]


@pytest.mark.asyncio
async def test_capture_records_kind():
    cap = PublicReplyCapture()
    await cap.reply("hi", kind="text")
    await cap.reply("card payload", kind="card")
    assert cap.kinds == ["text", "card"]


@pytest.mark.asyncio
async def test_capture_strips_whitespace_and_drops_empty():
    cap = PublicReplyCapture()
    await cap.reply("  带空格的回复  ")
    await cap.reply("")
    await cap.reply("   ")
    assert cap.public_replies == ["带空格的回复"]


@pytest.mark.asyncio
async def test_capture_case_insensitive_prefix():
    cap = PublicReplyCapture()
    await cap.reply("[INTERNAL] also blocked")
    await cap.reply("[Audit] blocked too")
    assert cap.public_replies == []
