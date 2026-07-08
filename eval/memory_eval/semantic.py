"""Limited reference-free semantic evaluators (Spec 4 §3.3, §10).

Judge timeout/error is reported separately and never changes product pass/fail.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from eval.memory_eval.metrics.types import MetricResult
from eval.memory_eval.schema import MultiturnScenario, ScenarioRun

logger = logging.getLogger(__name__)

_DIMENSIONS = [
    ("semantic_relevance", "Is the assistant reply relevant to the user's intent?"),
    ("standalone_query_quality", "Is the standalone query a well-formed standalone question?"),
    ("final_outcome_quality", "Does the final reply resolve the user's goal?"),
    ("conversation_naturalness", "Is the conversation natural and coherent?"),
]


async def _judge_one(judge, prompt: str, timeout_seconds: float) -> tuple[float, str | None]:
    try:
        raw = await asyncio.wait_for(
            judge.generate([{"role": "user", "content": prompt}]), timeout=timeout_seconds,
        )
        verdict = (raw or "").strip().upper()
        score = 1.0 if "PASS" in verdict or "GOOD" in verdict else 0.0
        return score, None
    except asyncio.TimeoutError:
        return 0.0, "judge timeout"
    except Exception as exc:  # noqa: BLE001
        logger.warning("semantic judge error: %s", exc)
        return 0.0, f"judge error: {exc}"


async def evaluate_semantic(
    pairs: list[tuple[MultiturnScenario, ScenarioRun]],
    *,
    judge: Any,
    timeout_seconds: float = 10.0,
) -> list[MetricResult]:
    results: dict[str, list[float]] = {name: [] for name, _ in _DIMENSIONS}
    for scenario, run in pairs:
        last_reply = run.observed[-1].replies[-1] if run.observed and run.observed[-1].replies else ""
        query = run.observed[-1].result.get("standalone_query", "") if run.observed else ""
        for name, question in _DIMENSIONS:
            prompt = f"{question}\nQuery: {query}\nReply: {last_reply}\nAnswer PASS or FAIL."
            score, err = await _judge_one(judge, prompt, timeout_seconds)
            if err:
                # Record one error result and stop collecting for this dimension.
                results[name] = [0.0]
                results[name + "_error"] = err  # type: ignore[assignment]
                break
            results[name].append(score)

    out: list[MetricResult] = []
    for name, _ in _DIMENSIONS:
        scores = results[name]
        err = results.get(name + "_error")
        if err:
            out.append(MetricResult(name=name, score=0.0, error=err))
        elif scores:
            avg = sum(scores) / len(scores)
            out.append(MetricResult(name=name, numerator=int(round(avg * len(scores))),
                                    denominator=len(scores), score=avg))
    return out


__all__ = ["evaluate_semantic"]
