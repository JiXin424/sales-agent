from __future__ import annotations

import json

import pytest

from eval.memory_eval.model_double import (
    DeterministicEmbeddingDouble,
    ScriptedModelDouble,
    TurnScript,
)


def _double():
    return ScriptedModelDouble(scripts={
        ("s1", 0): TurnScript(
            context_decision={"turn_relation": "new", "standalone_query": "我负责华东区", "retained_entities": []},
            chat_reply="好的，已为您记住华东区。",
            extraction={"candidates": []},
        ),
    })


async def test_json_call_returns_context_decision():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate(
        [{"role": "system", "content": "Decide turn_relation for the conversation."}],
        response_format={"type": "json_object"},
    )
    assert json.loads(out)["turn_relation"] == "new"


async def test_plain_call_returns_chat_reply():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate([{"role": "user", "content": "hi"}])
    assert out == "好的，已为您记住华东区。"


async def test_extraction_call_when_prompt_mentions_memory():
    d = _double()
    d.set_turn("s1", 0)
    out = await d.generate(
        [{"role": "system", "content": "Extract memory candidates from the user message."}],
        response_format={"type": "json_object"},
    )
    assert json.loads(out)["candidates"] == []


@pytest.mark.asyncio
async def test_embedding_is_deterministic():
    e = DeterministicEmbeddingDouble(dim=8)
    a = await e.embed(["hello", "world"])
    b = await e.embed(["hello", "world"])
    assert a == b
    assert len(a) == 2 and len(a[0]) == 8
    assert a[0] != a[1]  # different inputs → different vectors
