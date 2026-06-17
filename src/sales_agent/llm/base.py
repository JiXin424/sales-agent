"""LLM provider abstract base classes."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class ChatModel(ABC):
    """Chat model abstract base class."""

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        """Generate chat completion, return the content string.

        Args:
            messages: List of message dicts with ``role`` and ``content`` keys.
            temperature: Sampling temperature. Provider default used when *None*.
            max_tokens: Maximum tokens in the response. Provider default used when *None*.
            response_format: Optional format hint, e.g. ``{"type": "json_object"}``.

        Returns:
            The assistant content string from the completion.
        """
        ...

    async def stream_generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion, yielding content chunks as they arrive.

        Default implementation falls back to :meth:`generate` and yields
        the full string in one chunk.  Subclasses should override for
        true streaming.

        Args:
            messages: List of message dicts with ``role`` and ``content`` keys.
            temperature: Sampling temperature. Provider default used when *None*.
            max_tokens: Maximum tokens in the response. Provider default used when *None*.

        Yields:
            Content string chunks as they are produced by the model.
        """
        # Fallback: generate full response and yield once.
        content = await self.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield content


class EmbeddingModel(ABC):
    """Embedding model abstract base class."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: Strings to embed.

        Returns:
            Parallel list of embedding vectors.
        """
        ...


class ModelProvider:
    """Aggregates chat and embedding models behind a single interface."""

    def __init__(self, chat: ChatModel, embedding: EmbeddingModel) -> None:
        self.chat = chat
        self.embedding = embedding
