"""回归测试：跨轮澄清合并不得复活已填字段的 missing_fields（_merge_partial）。

守护 bug：`missing_fields=[... ] or old.missing_fields` 在 new 已补全（missing
为空）时会把旧的 missing 复活。于是一旦某轮把 ["title"] 塞进 pending_partial，
之后每轮合并即使 title 已给出仍被 validate 判 missing_title，永久卡死。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sales_agent.services.sales_actions.contracts import (
    SalesActionExtraction,
    SalesActionScope,
)
from sales_agent.services.sales_actions.service import SalesActionService
from sales_agent.services.sales_actions.time_parser import validate_action_extraction

_NOW = datetime(2026, 7, 10, 16, 56, 0, tzinfo=timezone.utc)  # = 2026-07-11 00:56 +08:00


def _ex(**kw) -> SalesActionExtraction:
    base = dict(
        intent="create_action",
        explicit_create=True,
        title="",
        action_type="other",
        scheduled_at=None,
        timezone="Asia/Shanghai",
        confidence=0.9,
        missing_fields=[],
    )
    base.update(kw)
    return SalesActionExtraction(**base)


def test_merge_does_not_resurrect_filled_missing_field():
    """旧 partial 报 title 缺失，新轮给出 title → 合并后 title 不得再算缺失。"""
    old = _ex(title="", missing_fields=["title"], confidence=0.8)
    new = _ex(title="测试", scheduled_at="2026-07-11T00:57:00+08:00", missing_fields=[])
    merged = SalesActionService._merge_partial(old, new)
    assert merged.title == "测试"
    assert "title" not in merged.missing_fields
    # 端到端：应能建提醒而非再次澄清
    decision = validate_action_extraction(merged, now=_NOW)
    assert decision.action == "create", decision.reason_code


def test_merge_preserves_genuinely_missing_field():
    """两轮都没给时间 → 合并后 scheduled_at 仍应算缺失（澄清流程不被误伤）。"""
    old = _ex(title="跟进王总", missing_fields=["scheduled_at"], confidence=0.8)
    new = _ex(title="", missing_fields=["scheduled_at"], confidence=0.6)
    merged = SalesActionService._merge_partial(old, new)
    assert merged.title == "跟进王总"
    assert "scheduled_at" in merged.missing_fields
    decision = validate_action_extraction(merged, now=_NOW)
    assert decision.action == "clarify"


def test_merge_fills_time_from_new_turn():
    """典型澄清：旧有 title 缺时间，新轮回答时间 → 合并后可建提醒。"""
    old = _ex(title="给张总回电话", missing_fields=["scheduled_at"], confidence=0.85)
    new = _ex(title="", scheduled_at="2026-07-11T09:00:00+08:00", missing_fields=[])
    merged = SalesActionService._merge_partial(old, new)
    assert merged.title == "给张总回电话"
    assert merged.scheduled_at == "2026-07-11T09:00:00+08:00"
    assert merged.missing_fields == []
    decision = validate_action_extraction(merged, now=_NOW)
    assert decision.action == "create", decision.reason_code


class _CannedModel:
    """返回固定 JSON 的假 chat_model。"""

    def __init__(self, payload: dict):
        self._payload = payload

    async def generate(self, messages, **kw):
        return json.dumps(self._payload, ensure_ascii=False)


@pytest.fixture(scope="module", autouse=True)
def _prompts_loaded():
    from sales_agent.llm.prompt_loader import load_prompts

    load_prompts("config/llm_config.yaml")


@pytest.mark.asyncio
async def test_complete_new_request_bypasses_stale_partial():
    """一条完整的显式建提醒请求不得被陈旧 partial（空标题/过期时间）污染。"""
    scope = SalesActionScope(tenant_id="t", agent_id="a", user_id="u")
    stale = SalesActionExtraction(
        intent="create_action", explicit_create=True, title="",
        action_type="other", scheduled_at="2026-07-11T00:50:00+08:00",
        timezone="Asia/Shanghai", confidence=0.8, missing_fields=["title"],
    ).model_dump()
    fresh = {
        "intent": "create_action", "explicit_create": True, "title": "测试",
        "customer_name": None, "action_type": "other", "time_text": "1分钟后",
        "scheduled_at": "2026-07-11T00:57:00+08:00", "timezone": "Asia/Shanghai",
        "confidence": 0.95, "missing_fields": [], "needs_clarification": False,
        "clarification_question": None,
    }
    svc = SalesActionService(repo=None, chat_model=_CannedModel(fresh))
    result = await svc._extract_with_merge(
        scope, "1分钟后提醒我测试", "conv-1", _NOW, pending_partial=stale
    )
    assert result.title == "测试"
    assert result.scheduled_at == "2026-07-11T00:57:00+08:00"
    assert result.missing_fields == []
    decision = validate_action_extraction(result, now=_NOW)
    assert decision.action == "create", decision.reason_code
