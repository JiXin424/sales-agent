"""DeepEval 集成测试 — 用 assert_test() 做 LLM-as-Judge 白盒评估。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "eval"))
import pytest
from deepeval import assert_test
from deepeval_test_cases import QuestionItem, call_agent_pipeline, build_llm_test_case, init_eval_db
from deepeval_metrics import (
    get_metrics_for_question, make_correctness_metric,
    make_faithfulness_metric, make_answer_relevancy_metric,
    make_task_completion_metric,
)

@pytest.fixture(scope="session", autouse=True)
def setup_deepeval():
    """初始化 DB 连接（全 session 只执行一次）。"""
    init_eval_db()

class TestKnowledgeQA:
    """知识问答场景测试。"""

    @pytest.mark.asyncio
    async def test_knowledge_qa_correctness(self):
        """有参考答案时，回答应与预期一致。"""
        q = QuestionItem(
            id="test_qa_001", text="MAX卡支持哪些超市？",
            reference="MAX卡支持山姆、盒马、叮咚等超市，最低折扣可达8折。",
            has_reference=True,
        )
        resp = await call_agent_pipeline(q, tenant_id="taishan")
        tc = build_llm_test_case(q, resp)
        metrics = [make_correctness_metric(), make_faithfulness_metric()]
        assert_test(tc, metrics)

    @pytest.mark.asyncio
    async def test_knowledge_qa_no_hallucination(self):
        """无参考答案时，回答不应编造信息。"""
        q = QuestionItem(
            id="test_qa_002", text="客户说太贵了，怎么回复？",
            has_reference=False,
        )
        resp = await call_agent_pipeline(q, tenant_id="taishan")
        tc = build_llm_test_case(q, resp)
        metrics = [make_faithfulness_metric(), make_answer_relevancy_metric()]
        assert_test(tc, metrics)


class TestFastCommands:
    """快速命令测试。"""

    @pytest.mark.asyncio
    async def test_help_command(self):
        """发送"帮助"应返回帮助文本。"""
        q = QuestionItem(id="test_help", text="帮助", has_reference=False)
        resp = await call_agent_pipeline(q, tenant_id="taishan")
        # 快速命令直接返回 fast_reply，不经 LLM 生成
        assert resp.task_type == "fast_command"
        assert len(resp.answer_text) > 0


class TestTaskCompletion:
    """Agentic 指标测试。"""

    @pytest.mark.asyncio
    async def test_answer_solves_user_problem(self):
        """回答应完成用户请求的任务。"""
        q = QuestionItem(
            id="test_completion_001",
            text="帮我写一段拜访后的跟进话术",
            has_reference=False,
        )
        resp = await call_agent_pipeline(q, tenant_id="taishan")
        tc = build_llm_test_case(q, resp)
        assert_test(tc, [make_task_completion_metric()])
