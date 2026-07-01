"""Lightweight tasks using LangGraph Functional API (@entrypoint / @task).

These are standalone tasks that can be used without a full StateGraph.
Suitable for simple, stateless operations like batch scoring or data transforms.

P0: ``entrypoint`` accepts ``checkpointer`` and ``store`` for persistence.
P3: ``entrypoint.final`` support for decoupling return value from saved state.
"""

from __future__ import annotations

from langgraph.func import entrypoint, task
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore


@task
def score_single_dimension(dimension: str, conversation_text: str) -> dict:
    """Score one competency dimension from a conversation.

    This is a ``@task`` — it can be called in parallel with other dimensions.

    Args:
        dimension: Dimension name (e.g. "needs_discovery").
        conversation_text: The conversation text to score.

    Returns:
        A dict with the dimension name and score.
    """
    # Stub — real implementation calls LLM
    return {"dimension": dimension, "score": 50}


# P3: entrypoint with optional checkpointer for state persistence
@entrypoint(checkpointer=InMemorySaver(), store=InMemoryStore())
def batch_score_conversations(dimensions: list[str], conversations: list[str]) -> list[dict]:
    """Score multiple conversations across all dimensions in parallel.

    Uses ``@task`` for parallelism: each (dimension, conversation) pair
    is scored concurrently. With checkpointer, results are cached across
    retries.

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


# P0: HITL example — batch scoring with human review gate
@entrypoint(checkpointer=InMemorySaver())
def review_workflow(topic: str) -> entrypoint.final[dict, dict]:
    """Example HITL workflow using functional API.

    Generates an essay, then pauses for human review via ``interrupt()``.
    Uses ``entrypoint.final`` to decouple return value from saved state.

    Args:
        topic: The subject to address.

    Returns:
        entrypoint.final with the visible result and the persisted state.
    """
    # Simulate work
    result = {"topic": topic, "status": "draft", "content": f"Analysis of {topic}"}

    # Pause for human review
    review = interrupt({
        "question": "Please review this draft",
        "draft": result,
    })

    result["review"] = review
    result["status"] = "reviewed"

    # P3: return visible result, save different value for next invocation
    return entrypoint.final(
        value=result,
        save={"last_topic": topic, "last_result": result},
    )
