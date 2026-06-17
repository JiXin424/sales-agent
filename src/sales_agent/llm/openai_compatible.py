"""OpenAI-compatible LLM provider implementation using the ``openai`` async SDK."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError

from sales_agent.core.exceptions import ModelFailedError
from sales_agent.llm.base import ChatModel, EmbeddingModel

logger = logging.getLogger(__name__)

# Maximum texts per single embedding API call.
_EMBEDDING_BATCH_SIZE = 10  # 阿里云 dashscope embedding 上限为 10


class OpenAICompatibleChat(ChatModel):
    """Chat completion via any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        timeout_seconds: int = 30,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout_seconds),
            max_retries=max_retries,
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        effective_temp = temperature if temperature is not None else self._temperature

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": effective_temp,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        last_exc: Exception | None = None
        for attempt in range(self._client.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if content is None:
                    raise ModelFailedError("Model returned empty content")
                return content
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                last_exc = exc
                if attempt < self._client.max_retries:
                    delay = 0.5 * (2 ** attempt)
                    logger.warning(
                        "Chat API %s (attempt %d/%d), retrying in %.1fs",
                        type(exc).__name__,
                        attempt + 1,
                        self._client.max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        raise ModelFailedError(
            detail=f"Chat API failed after {self._client.max_retries + 1} attempts: {last_exc}"
        )

    async def stream_generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion, yielding content chunks incrementally.

        Uses the OpenAI streaming API (``stream=True``) to receive tokens
        incrementally and yields them immediately as they arrive.

        Yields:
            Content string chunks as they arrive from the API.
        """
        effective_temp = temperature if temperature is not None else self._temperature

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": effective_temp,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        # 生成一个用于去重的 retry 边界标记。
        # 因为 async generator 不能在 yield 后 retry（已 yield 的内容无法撤回），
        # 所以只在首个 chunk 到达前 retry。一旦开始 yield 就不再重试。
        last_exc: Exception | None = None
        for attempt in range(self._client.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                async for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
                return  # 成功完成，退出 generator
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                last_exc = exc
                if attempt < self._client.max_retries:
                    delay = 0.5 * (2 ** attempt)
                    logger.warning(
                        "Stream Chat API %s (attempt %d/%d), retrying in %.1fs",
                        type(exc).__name__,
                        attempt + 1,
                        self._client.max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        raise ModelFailedError(
            detail=f"Stream Chat API failed after {self._client.max_retries + 1} attempts: {last_exc}"
        )


class OpenAICompatibleEmbedding(EmbeddingModel):
    """Embedding generation via any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout_seconds),
            max_retries=max_retries,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        batches = [
            texts[i : i + _EMBEDDING_BATCH_SIZE]
            for i in range(0, len(texts), _EMBEDDING_BATCH_SIZE)
        ]

        for batch_idx, batch in enumerate(batches):
            last_exc: Exception | None = None
            for attempt in range(self._client.max_retries + 1):
                try:
                    response = await self._client.embeddings.create(
                        model=self._model,
                        input=batch,
                    )
                    batch_embeddings = [item.embedding for item in response.data]
                    all_embeddings.extend(batch_embeddings)
                    break
                except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                    last_exc = exc
                    if attempt < self._client.max_retries:
                        delay = 0.5 * (2 ** attempt)
                        logger.warning(
                            "Embedding API %s (batch %d, attempt %d/%d), retrying in %.1fs",
                            type(exc).__name__,
                            batch_idx + 1,
                            attempt + 1,
                            self._client.max_retries + 1,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise ModelFailedError(
                            detail=(
                                f"Embedding API failed on batch {batch_idx + 1} "
                                f"after {self._client.max_retries + 1} attempts: {last_exc}"
                            )
                        ) from last_exc

        return all_embeddings
