"""Deterministic model doubles for the graph-multiturn layer (Spec 4 §3.2).

Conform to the real interfaces in ``src/sales_agent/llm/base.py``:
``ChatModel.generate`` and ``EmbeddingModel.embed`` (both ``async``).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Optional

from sales_agent.llm.base import ChatModel, EmbeddingModel


@dataclass
class TurnScript:
    """Canned responses for one scenario turn, one per call site."""

    context_decision: dict
    chat_reply: str
    extraction: dict = field(default_factory=lambda: {"candidates": []})


class ScriptedModelDouble(ChatModel):
    """Returns scripted JSON / replies based on the call site.

    Call-site detection:
      * ``response_format == {"type": "json_object"}`` AND prompt mentions
        ``turn_relation``/``standalone_query`` → context-resolver decision.
      * ``response_format == {"type": "json_object"}`` AND prompt mentions
        ``memory``/``extract`` → extractor result.
      * otherwise → chat reply.
    """

    def __init__(self, scripts: dict[tuple[str, int], TurnScript]) -> None:
        self._scripts = scripts
        self._current: Optional[TurnScript] = None

    def set_turn(self, scenario_id: str, turn_index: int) -> None:
        key = (scenario_id, turn_index)
        if key not in self._scripts:
            raise KeyError(f"no script for {key}")
        self._current = self._scripts[key]

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        assert self._current is not None, "set_turn() not called"
        blob = " ".join(m.get("content", "") for m in messages).lower()
        if response_format and response_format.get("type") == "json_object":
            if "turn_relation" in blob or "standalone_query" in blob:
                return json.dumps(self._current.context_decision, ensure_ascii=False)
            if "memory" in blob or "extract" in blob:
                return json.dumps(self._current.extraction, ensure_ascii=False)
            # Default structured call → context decision shape.
            return json.dumps(self._current.context_decision, ensure_ascii=False)
        return self._current.chat_reply

    async def stream_generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        yield await self.generate(messages, temperature=temperature, max_tokens=max_tokens)


class DeterministicEmbeddingDouble(EmbeddingModel):
    """Hash-based fixed-dimensional embedding (no randomness, async)."""

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(digest[i % len(digest)] / 255.0) * 2 - 1 for i in range(self.dim)]
            out.append(vec)
        return out


__all__ = ["DeterministicEmbeddingDouble", "ScriptedModelDouble", "TurnScript"]
