"""验证 evidence_router prompt 含旧 intent_router 的检索原则。"""

from sales_agent.prompts.evidence_router_prompt import EVIDENCE_ROUTER_PROMPT


def test_evidence_router_has_none_lie_search_principle():
    """迁移后应含'宁可多搜不可漏搜'原则。"""
    assert "宁可多搜" in EVIDENCE_ROUTER_PROMPT or "不可漏搜" in EVIDENCE_ROUTER_PROMPT


def test_evidence_router_has_trigger_table():
    """应含触发词对照表。"""
    assert "knowledge_policy" in EVIDENCE_ROUTER_PROMPT
    assert "required" in EVIDENCE_ROUTER_PROMPT


def test_evidence_router_schema_unchanged():
    for field in ("intent", "response_mode", "knowledge_policy", "retrieval_query", "confidence"):
        assert field in EVIDENCE_ROUTER_PROMPT