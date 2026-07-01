"""DeepEval pytest 辅助模块 — 提供评估相关的 fixture。"""
from __future__ import annotations
import pytest
from deepeval_test_cases import QuestionItem, call_agent_pipeline, build_llm_test_case, init_eval_db
from deepeval_metrics import get_metrics_for_question

@pytest.fixture(scope="session", autouse=True)
def deepeval_db_init():
    """Session 级 fixture：自动初始化 DB 连接。"""
    init_eval_db()

async def eval_question(question_text: str, reference: str = "", tenant_id: str = "taishan"):
    """快捷函数：给定问题文本，返回评估就绪的 (test_case, metrics)。"""
    q = QuestionItem(
        id="adhoc", text=question_text, reference=reference,
        has_reference=bool(reference),
    )
    resp = await call_agent_pipeline(q, tenant_id=tenant_id)
    tc = build_llm_test_case(q, resp)
    metrics = get_metrics_for_question(q.has_reference)
    return tc, metrics, resp
