"""Lightweight tasks using LangGraph Functional API (@entrypoint / @task).

These are standalone tasks that can be used without a full StateGraph.
Suitable for simple, stateless operations like batch scoring or data transforms.
"""

from __future__ import annotations

from langgraph.func import entrypoint, task


@task
def score_single_dimension(dimension: str, conversation_text: str) -> dict:
    """Score one competency dimension from a conversation.

    This is a ``@task`` -- it can be called in parallel with other dimensions.

    Args:
        dimension: Dimension name (e.g. "needs_discovery").
        conversation_text: The conversation text to score.

    Returns:
        A dict with the dimension name and score.
    """
    # Stub -- real implementation calls LLM
    return {"dimension": dimension, "score": 50}


@entrypoint()
def batch_score_conversations(dimensions: list[str], conversations: list[str]) -> list[dict]:
    """Score multiple conversations across all dimensions in parallel.

    Uses ``@task`` for parallelism: each (dimension, conversation) pair
    is scored concurrently via Send fan-out.

    Args:
        dimensions: List of dimension names to score.
        conversations: List of conversation texts to score.

    Returns:
        List of score dicts, one per (dimension, conversation) pair.
    """
    futures = [
        score_single_dimension(dim, conv)
        for dim in dimensions
        for conv in conversations
    ]
    return [f.result() for f in futures]
