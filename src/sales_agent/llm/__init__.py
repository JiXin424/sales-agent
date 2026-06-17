"""LLM provider abstraction layer."""

from sales_agent.llm.base import ChatModel, EmbeddingModel, ModelProvider
from sales_agent.llm.openai_compatible import OpenAICompatibleChat, OpenAICompatibleEmbedding

__all__ = [
    "ChatModel",
    "EmbeddingModel",
    "ModelProvider",
    "OpenAICompatibleChat",
    "OpenAICompatibleEmbedding",
]
