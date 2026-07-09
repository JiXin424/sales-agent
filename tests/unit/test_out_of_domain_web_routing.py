"""Tests for out-of-domain → web search routing.

Locks the plumbing that lets clearly out-of-sales-domain questions
(sports results, news, general knowledge, tech/code) route to Bocha web
search instead of the knowledge base, plus the precision guardrails:
- `knowledge_policy="web"` is a first-class policy.
- the policy guard must NOT upgrade/downgrade a `web` decision.
- the bare `vs` regex no longer force-upgrades sports comparisons to KB.
- `select_retrieval_path` sends `web` policy to retrieve with a web hint.
- `retrieve_node` runs web search as the PRIMARY path (not only a fallback).

Prompt-level precision (which queries are "out of domain") is validated
on the deployed tenant with real examples; these tests lock the machinery.
"""

import pytest
from langgraph.runtime import Runtime
from langgraph.types import Send

from sales_agent.graph.chat.edges import select_retrieval_path
from sales_agent.graph.chat.nodes import retrieval as retrieval_mod
from sales_agent.graph.chat.nodes.retrieval import retrieve_node
from sales_agent.services.evidence_router import apply_evidence_policy_guard
from sales_agent.services.structured_router_output import EvidenceDecision


# ---------------------------------------------------------------------------
# 1. Schema: `web` is a valid knowledge_policy
# ---------------------------------------------------------------------------

def test_web_policy_is_valid_with_retrieval_query():
    decision = EvidenceDecision(
        intent="knowledge_qa",
        response_mode="retrieve",
        knowledge_policy="web",
        knowledge_scope=[],
        retrieval_query="阿根廷和埃及比赛结果",
        confidence=0.8,
        reason_code="out_of_domain_web",
    )
    assert decision.knowledge_policy == "web"


def test_web_policy_requires_retrieval_query():
    with pytest.raises(ValueError):
        EvidenceDecision(
            intent="knowledge_qa",
            response_mode="retrieve",
            knowledge_policy="web",
            knowledge_scope=[],
            retrieval_query=None,
            confidence=0.8,
            reason_code="out_of_domain_web",
        )


# ---------------------------------------------------------------------------
# 2. Policy guard: web is sticky; bare `vs` no longer upgrades sports
# ---------------------------------------------------------------------------

def _decision(policy: str, query: str) -> EvidenceDecision:
    return EvidenceDecision(
        intent="knowledge_qa",
        response_mode="retrieve" if policy != "none" else "direct",
        knowledge_policy=policy,
        knowledge_scope=[],
        retrieval_query=query if policy in ("required", "web") else None,
        confidence=0.8,
        reason_code="x",
    )


def test_guard_keeps_web_policy_on_sports_vs_query():
    """A web decision for a sports 'X vs Y' query must survive the guard."""
    decision = apply_evidence_policy_guard("阿根廷和埃及谁赢了", _decision("web", "阿根廷埃及比赛"))
    assert decision.knowledge_policy == "web"


def test_vs_no_longer_upgrades_sports_to_required():
    """Regression: bare `vs` used to force-upgrade sports to KB (required)."""
    decision = apply_evidence_policy_guard("阿根廷vs埃及谁赢了", _decision("none", ""))
    assert decision.knowledge_policy != "required"   # was upgraded via literal `vs`


def test_guard_does_not_downgrade_web_on_non_fact_signal():
    """web must not be downgraded to none by a non-fact (greeting) signal."""
    decision = apply_evidence_policy_guard("你好", _decision("web", "some web query"))
    assert decision.knowledge_policy == "web"


def test_guard_still_upgrades_in_domain_required_unchanged():
    """In-domain fact signals still keep required (no regression on KB path)."""
    decision = apply_evidence_policy_guard("福多多和东方福利网对比", _decision("required", "福多多 东方福利网 对比"))
    assert decision.knowledge_policy == "required"


# ---------------------------------------------------------------------------
# 3. select_retrieval_path: web policy → web Send
# ---------------------------------------------------------------------------

def test_select_retrieval_path_routes_web_policy_to_web_retrieve():
    state = {
        "needs_retrieval": True,
        "knowledge_policy": "web",
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "阿根廷和埃及谁赢了",
        "task_type": "knowledge_qa",
        "agent_id": None,
        "conversation_id": "c1",
        "channel": "local",
    }
    result = select_retrieval_path(state)
    assert isinstance(result, list) and len(result) == 1
    send = result[0]
    assert isinstance(send, Send) and send.node == "retrieve"
    assert send.arg["retrieval_path"] == "web"


def test_select_retrieval_path_skips_when_web_disabled_by_no_retrieval():
    """If needs_retrieval is False, web policy must not retrieve."""
    state = {
        "needs_retrieval": False,
        "knowledge_policy": "web",
        "tenant_id": "t1", "user_id": "u1",
        "message": "x", "conversation_id": "c1", "channel": "local",
    }
    assert select_retrieval_path(state) == "skip"


# ---------------------------------------------------------------------------
# 4. retrieve_node: web path runs web search as PRIMARY (not only fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_node_web_path_calls_web_search(monkeypatch):
    called = {"web": False}

    async def _fake_web_fallback(*, message, tenant_id, runtime, api_key, top_n):
        called["web"] = True
        return {
            "sources": [{"title": "阿根廷 vs 埃及赛果", "source_type": "web", "score": 0.9}],
            "ontology_context_text": "网络搜索：阿根廷 2:1 埃及",
        }

    monkeypatch.setattr(retrieval_mod, "web_fallback_and_analyze", _fake_web_fallback)

    runtime = Runtime(context={})
    state = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "阿根廷和埃及谁赢了", "conversation_id": "c1", "channel": "local",
        "retrieval_path": "web",
        "task_type": "knowledge_qa",
    }
    result = await retrieve_node(state, runtime)

    assert called["web"] is True
    assert result["sources"] and result["sources"][0]["source_type"] == "web"
    assert result["retrieval_info"]["web_search_used"] is True


# ---------------------------------------------------------------------------
# 5. Prompt precision guardrails (structural): web policy + domain boundary
#    documented so the LLM has an out-of-domain exit and a conservative rule.
# ---------------------------------------------------------------------------

def test_evidence_router_prompt_documents_web_policy_and_domain_boundary():
    from sales_agent.prompts.evidence_router_prompt import EVIDENCE_ROUTER_PROMPT as p
    # web policy exists
    assert "web" in p
    # conservative rule: uncertain → KB (never lose KB for a real sales question)
    assert "不确定" in p or "明确" in p
    # explicit out-of-domain examples the user hit (sports / code)
    assert "体育" in p or "赛" in p
