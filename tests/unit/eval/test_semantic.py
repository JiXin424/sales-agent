from __future__ import annotations

import asyncio

import pytest

from eval.memory_eval.semantic import evaluate_semantic
from eval.memory_eval.schema import (
    ExpectedTurn, MultiturnScenario, ObservedTurn, ScenarioRun, ScenarioTurn,
)


def _pair():
    s = MultiturnScenario(id="s1", turns=[ScenarioTurn(input="t0", expected=ExpectedTurn(reply_contains=["华东"]))])
    run = ScenarioRun(scenario_id="s1", observed=[ObservedTurn(
        turn_index=0, result={"standalone_query": "我负责华东区"},
        replies=["好的，华东区"], active_topic_ids=[], closed_topic_ids=[],
        active_memory_keys=[], selected_memory_ids=[],
    )], final_state={})
    return [(s, run)]


class _FakeJudge:
    def __init__(self, reply): self._reply = reply
    async def generate(self, messages, **kw): return self._reply


class _TimeoutJudge:
    async def generate(self, messages, **kw):
        await asyncio.sleep(10)
        return "PASS"


@pytest.mark.asyncio
async def test_semantic_records_scores():
    metrics = await evaluate_semantic(_pair(), judge=_FakeJudge("PASS"), timeout_seconds=2.0)
    by_name = {m.name: m for m in metrics}
    assert by_name["semantic_relevance"].error is None
    assert by_name["semantic_relevance"].applicable is True


@pytest.mark.asyncio
async def test_judge_timeout_is_reported_not_fatal():
    metrics = await evaluate_semantic(_pair(), judge=_TimeoutJudge(), timeout_seconds=0.05)
    by_name = {m.name: m for m in metrics}
    assert by_name["semantic_relevance"].error is not None
    assert by_name["semantic_relevance"].passes is True   # error never flips gate (§10)


@pytest.mark.asyncio
async def test_not_pass_scores_zero():
    # "NOT PASS" must NOT be scored as a pass (§10): the verdict is a negative,
    # so the score must be 0.0, not 1.0. A bare substring check would wrongly
    # match the "PASS" inside "NOT PASS".
    metrics = await evaluate_semantic(_pair(), judge=_FakeJudge("NOT PASS"), timeout_seconds=2.0)
    by_name = {m.name: m for m in metrics}
    assert by_name["semantic_relevance"].error is None
    assert by_name["semantic_relevance"].score == 0.0
