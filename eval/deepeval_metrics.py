"""
DeepEval 评估指标配置 —— 针对 Sales Agent（ToB 销售陪跑）场景定制。

指标设计原则（方案 C）：
- 有参考答案的题目：GEval(正确性) + GEval(完整性) + Faithfulness + AnswerRelevancy + AnswerRecall
- 无参考答案的题目：Faithfulness + AnswerRelevancy + Hallucination + AnswerRecall
- AnswerRecall 是新增的自定义指标：对比 retrieval_context → actual_output 的信息覆盖度

评估 LLM（裁判模型）配置：
  评估需要一个独立的 LLM 来当"裁判"给 Agent 的回答打分。
  默认使用 OpenAI GPT-4o（需要 OPENAI_API_KEY），也支持自定义。
"""

import os
from typing import List, Optional

from deepeval.metrics import (
    GEval,
    FaithfulnessMetric,
    AnswerRelevancyMetric,
    HallucinationMetric,
    BaseMetric,
    ArenaGEval,
)
from deepeval.models import GPTModel, DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams, ArenaTestCase, Contestant
from deepeval.metrics.utils import (
    check_llm_test_case_params,
    initialize_model,
    generate_with_schema_and_extract,
    a_generate_with_schema_and_extract,
    construct_verbose_logs,
)
from deepeval.metrics.indicator import metric_progress_indicator
from deepeval.utils import get_or_create_event_loop, prettify_list
from pydantic import BaseModel


# ── 裁判模型 ─────────────────────────────────────────────────────

def get_judge_model(
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """获取评估用的裁判 LLM 模型。

    优先级：参数 > 环境变量 > 默认值 (GPT-4o)

    Args:
        model_name: 模型名，如 "gpt-4o" / "qwen-plus" / "deepseek-chat"
        api_key: API Key
        base_url: OpenAI 兼容的 Base URL

    Returns:
        DeepEvalBaseLLM 实例

    Raises:
        RuntimeError: 如果没有任何可用的 API Key
    """
    model_name = model_name or os.getenv("DEEPEVAL_MODEL") or "gpt-4o"
    api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPEVAL_API_KEY")

    if api_key:
        kwargs = {"model": model_name, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return GPTModel(**kwargs)

    # 尝试加载 DeepEval 已配置的默认模型
    try:
        return GPTModel(model=model_name)
    except Exception:
        pass

    raise RuntimeError(
        "无法初始化评估裁判模型。请设置以下任一环境变量：\n"
        "  - OPENAI_API_KEY=sk-...  （推荐，使用 GPT-4o）\n"
        "  - DEEPEVAL_API_KEY=... + DEEPEVAL_MODEL=model_name\n\n"
        "或者如果 Agent 模型支持 OpenAI 兼容接口（如 Qwen/DeepSeek），可以：\n"
        "  export OPENAI_API_KEY=<你的阿里云/DeepSeek API Key>\n"
        "  export DEEPEVAL_MODEL=qwen-plus\n"
        "  export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


_judge_model_cache: Optional[object] = None


def set_global_judge_model(model) -> None:
    """设置全局裁判模型（避免每次创建 metric 都初始化）。"""
    global _judge_model_cache
    _judge_model_cache = model


def _get_cached_judge():
    """获取已缓存的裁判模型，如果未设置则自动初始化。"""
    global _judge_model_cache
    if _judge_model_cache is None:
        _judge_model_cache = get_judge_model()
    return _judge_model_cache


# ── 有参考答案时使用的指标 ──────────────────────────────────────────

def make_correctness_metric(judge_model=None) -> GEval:
    """正确性：对比 Agent 输出与参考答案，评估内容是否准确可用。

    适用于有 explicit reference answer 的场景（如 qa_extracted.md 中的 QA 对）。
    """
    return GEval(
        name="正确性 (Correctness)",
        criteria=(
            "你需要评估「实际输出」在多大程度上与「预期输出」一致。\n\n"
            "评估标准：\n"
            "1. 核心事实是否一致：实际输出中的关键数据、产品名称、政策说明是否与预期输出匹配\n"
            "2. 回答完整性：是否覆盖了预期输出中的关键要点（不要求逐字相同，但核心信息应覆盖）\n"
            "3. 销售场景的实用性：回答是否对销售人员在实战中有帮助\n\n"
            "打分指南：\n"
            "- 1.0：完全一致，所有关键信息都正确\n"
            "- 0.7-0.9：主要信息正确，有少量细节差异但不影响实用性\n"
            "- 0.5-0.6：方向正确但信息不完整或部分有误\n"
            "- 0.3-0.4：有重要事实错误\n"
            "- 0.0-0.2：完全不相关或完全错误"
        ),
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        threshold=0.5,
        model=judge_model or _get_cached_judge(),
    )


# ── 所有题目都使用的指标 ──────────────────────────────────────────

def make_faithfulness_metric(judge_model=None) -> FaithfulnessMetric:
    """忠实度：检查 Agent 回答是否基于检索到的知识库内容，而非凭空编造。

    这是评估知识库质量的核心指标——忠实度越高，说明知识库内容越能支撑回答。
    用于对比两套 KB（ontology_neo4j vs legacy_rag）时，这个指标最关键。
    """
    return FaithfulnessMetric(
        threshold=0.5,
        include_reason=True,
        model=judge_model or _get_cached_judge(),
    )


def make_answer_relevancy_metric(judge_model=None) -> AnswerRelevancyMetric:
    """相关性：检查 Agent 回答是否切合用户提问，答非所问则分数低。"""
    return AnswerRelevancyMetric(
        threshold=0.5,
        include_reason=True,
        model=judge_model or _get_cached_judge(),
    )


# ── 无参考答案时的补充指标 ──────────────────────────────────────

def make_hallucination_metric(judge_model=None) -> HallucinationMetric:
    """幻觉检测：检查回答是否包含与上下文矛盾的信息。"""
    return HallucinationMetric(
        threshold=0.5,
        include_reason=True,
        model=judge_model or _get_cached_judge(),
    )


# ── 方案 C 新增：AnswerRecall — 检索→回答的信息覆盖度 ─────────

class _RecallPoint(BaseModel):
    verdict: str   # "yes" = actual_output 覆盖了这个信息点
    reason: str


class _RecallPoints(BaseModel):
    points: List[_RecallPoint]


class AnswerRecallMetric(BaseMetric):
    """回答召回率：检索到的关键信息点在回答中被覆盖了多少。

    类比检索 recall@k，但方向是 检索内容 → 回答。
    不需要参考答案，只需要 retrieval_context（Agent 实际检索到的内容）。

    score = 被覆盖的信息点数 / 检索到的总信息点数
    """

    _required_params: List[SingleTurnParams] = [
        SingleTurnParams.INPUT,
        SingleTurnParams.ACTUAL_OUTPUT,
        SingleTurnParams.RETRIEVAL_CONTEXT,
    ]

    def __init__(
        self,
        threshold: float = 0.5,
        model: Optional[DeepEvalBaseLLM] = None,
        include_reason: bool = True,
    ):
        self.threshold = threshold
        self.model, self.using_native_model = initialize_model(model)
        self.evaluation_model = self.model.get_model_name()
        self.include_reason = include_reason

    def measure(
        self,
        test_case: LLMTestCase,
        _show_indicator: bool = True,
        _in_component: bool = False,
        _log_metric_to_confident: bool = True,
    ) -> float:
        check_llm_test_case_params(
            test_case, self._required_params, None, None, self, self.model, False
        )
        self.evaluation_cost = 0 if self.using_native_model else None
        with metric_progress_indicator(
            self, _show_indicator=_show_indicator, _in_component=_in_component
        ):
            loop = get_or_create_event_loop()
            loop.run_until_complete(
                self.a_measure(test_case, _show_indicator=False,
                               _in_component=_in_component,
                               _log_metric_to_confident=_log_metric_to_confident)
            )
        return self.score

    async def a_measure(
        self,
        test_case: LLMTestCase,
        _show_indicator: bool = True,
        _in_component: bool = False,
        _log_metric_to_confident: bool = True,
    ) -> float:
        check_llm_test_case_params(
            test_case, self._required_params, None, None, self.model, False
        )
        self.evaluation_cost = 0 if self.using_native_model else None
        with metric_progress_indicator(
            self, async_mode=True, _show_indicator=_show_indicator,
            _in_component=_in_component,
        ):
            # Step 1: 从 retrieval_context 提取关键信息点
            key_points = await self._extract_key_points(test_case.retrieval_context)

            if not key_points:
                self.score = 1.0
                self.reason = "检索内容为空，无法评估召回率"
                self.success = True
                return self.score

            # Step 2: 逐条判断 actual_output 是否覆盖了这些信息点
            self.verdicts: List[_RecallPoint] = await self._check_coverage(
                test_case.actual_output, key_points
            )

            self.score = self._calculate_score()
            self.reason = self._generate_reason()
            self.success = self.score >= self.threshold
            self.verbose_logs = construct_verbose_logs(
                self,
                steps=[
                    f"Key points from retrieval: {prettify_list(key_points)}",
                    f"Coverage verdicts: {prettify_list(self.verdicts)}",
                    f"Score: {self.score}\nReason: {self.reason}",
                ],
            )
            return self.score

    async def _extract_key_points(
        self, retrieval_context: List[str]
    ) -> List[str]:
        """从检索到的文档中提取关键信息点。"""
        if not retrieval_context:
            return []

        prompt = (
            "从以下检索到的知识库内容中，提取所有独立的「关键信息点」。\n"
            "每条信息点是一个独立的、可验证的事实陈述。\n"
            "以 JSON 数组格式返回，key 为 'points'。\n\n"
            "示例：\n"
            "检索内容: 'MAX卡支持山姆、盒马、叮咚，最低折扣可达8折。'\n"
            '输出: {"points": ["MAX卡支持山姆", "MAX卡支持盒马", '
            '"MAX卡支持叮咚", "MAX卡最低折扣8折"]}\n\n'
            f"检索内容:\n{chr(10).join(retrieval_context)}"
        )
        try:
            result = await a_generate_with_schema_and_extract(
                metric=self,
                prompt=prompt,
                schema_cls=type("_Points", (BaseModel,), {"points": List[str]}),
                extract_schema=lambda s: s.points,
                extract_json=lambda d: d["points"],
            )
            return result or []
        except Exception:
            return []

    async def _check_coverage(
        self, actual_output: str, key_points: List[str]
    ) -> List[_RecallPoint]:
        """逐条检查 actual_output 是否覆盖了每个关键信息点。"""
        if not key_points:
            return []

        points_text = "\n".join(
            f"{i+1}. {p}" for i, p in enumerate(key_points)
        )
        prompt = (
            "对于以下每个「关键信息点」，判断在「Agent 回答」中是否被提及或覆盖。\n"
            "不要求逐字相同，只要意思被覆盖就算 YES。\n"
            "以 JSON 格式返回，key 为 'points'，每个元素包含 'verdict' (yes/no) 和 'reason'。\n\n"
            f"Agent 回答:\n{actual_output}\n\n"
            f"关键信息点:\n{points_text}\n\n"
            '示例输出: {{"points": ['
            '{{"verdict": "yes", "reason": "回答中提到了山姆"}},'
            '{{"verdict": "no", "reason": "回答中未提及盒马"}}'
            ']}}'
        )
        try:
            result = await a_generate_with_schema_and_extract(
                metric=self,
                prompt=prompt,
                schema_cls=_RecallPoints,
                extract_schema=lambda s: list(s.points),
                extract_json=lambda d: [
                    _RecallPoint(**item) for item in d["points"]
                ],
            )
            return result or []
        except Exception:
            return []

    def _calculate_score(self) -> float:
        if not self.verdicts:
            return 1.0
        covered = sum(
            1 for v in self.verdicts
            if v.verdict.strip().lower() == "yes"
        )
        return covered / len(self.verdicts)

    def _generate_reason(self) -> str:
        if not self.verdicts:
            return "无检索内容"
        covered = sum(
            1 for v in self.verdicts
            if v.verdict.strip().lower() == "yes"
        )
        missing = [v.reason for v in self.verdicts
                    if v.verdict.strip().lower() == "no"]
        parts = [f"覆盖 {covered}/{len(self.verdicts)} 个信息点"]
        if missing:
            parts.append(f"遗漏: {'; '.join(missing[:5])}")
        return "。".join(parts)

    def is_successful(self) -> bool:
        if self.error is not None:
            return False
        try:
            return self.score >= self.threshold
        except TypeError:
            return False

    @property
    def __name__(self):
        return "AnswerRecall (回答召回率)"


# ── 有参考答案时的完整性指标 ──────────────────────────────────

def make_completeness_metric(judge_model=None) -> GEval:
    """完整性：对比参考答案，检查 Agent 回答是否遗漏了关键信息点。

    仅在有 explicit reference answer 时使用。
    """
    return GEval(
        name="完整性 (Completeness)",
        criteria=(
            "评估「实际输出」是否完整覆盖了「预期输出」中的所有关键信息点。\n\n"
            "评估标准：\n"
            "1. 预期输出中的每个事实/数据/要点，实际输出是否都提到了\n"
            "2. 不要求逐字相同，但核心信息不能遗漏\n"
            "3. 实际输出如果比预期多了一些相关补充信息，不扣分\n\n"
            "打分指南：\n"
            "- 1.0：所有关键信息点都覆盖了\n"
            "- 0.7-0.9：覆盖了大部分，缺 1-2 个次要信息点\n"
            "- 0.5-0.6：覆盖了主要信息点，但缺了几个重要信息点\n"
            "- 0.3-0.4：缺了很多关键信息\n"
            "- 0.0-0.2：几乎没有覆盖预期输出中的内容"
        ),
        evaluation_params=[
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        threshold=0.5,
        model=judge_model or _get_cached_judge(),
    )


# ── 指标组合工厂 ──────────────────────────────────────────────────

def get_metrics_for_question(
    has_reference: bool,
    judge_model=None,
):
    """方案 C：有参考答案 → 正确性+完整性；无答案 → 幻觉+召回。

    Args:
        has_reference: 是否有预期输出（参考答案）
        judge_model: 裁判 LLM 模型实例（可选）

    Returns:
        list: 指标实例列表
    """
    model = judge_model or _get_cached_judge()

    # 所有题目都用这些
    base = [
        make_faithfulness_metric(model),
        make_answer_relevancy_metric(model),
        AnswerRecallMetric(model=model, threshold=0.4),
    ]

    if has_reference:
        # 方案 A：+正确性 +完整性
        base.insert(0, make_completeness_metric(model))
        base.insert(0, make_correctness_metric(model))
    else:
        # 方案 B：+幻觉检测
        base.append(make_hallucination_metric(model))

    return base


# ── Arena 盲测对比 ─────────────────────────────────────────────

def run_arena_comparison(
    kb_a_label: str,
    kb_a_answer: str,
    kb_b_label: str,
    kb_b_answer: str,
    input_text: str,
    judge_model=None,
) -> dict:
    """对同一个问题的两个 KB 回答进行盲测对比。

    裁判 LLM 看到的是匿名输出（"模型A"/"模型B"），不会受到 KB 标签影响。

    Args:
        kb_a_label: KB A 的标签（如 "ontology_neo4j"）
        kb_a_answer: KB A 的回答文本
        kb_b_label: KB B 的标签（如 "legacy_rag"）
        kb_b_answer: KB B 的回答文本
        input_text: 用户问题
        judge_model: 裁判模型

    Returns:
        {"winner": "ontology_neo4j", "reason": "...", "error": None}
    """
    model = judge_model or _get_cached_judge()
    try:
        arena_tc = ArenaTestCase(contestants=[
            Contestant(
                name=kb_a_label,
                test_case=LLMTestCase(
                    input=input_text,
                    actual_output=kb_a_answer,
                ),
            ),
            Contestant(
                name=kb_b_label,
                test_case=LLMTestCase(
                    input=input_text,
                    actual_output=kb_b_answer,
                ),
            ),
        ])

        metric = ArenaGEval(
            name="SalesAgent Arena",
            criteria=(
                "评估两个 AI 销售教练的回答。请判断哪个回答更好。\n\n"
                "评估维度（按优先级排列）：\n"
                "1. 准确性：回答中的事实信息是否正确、不误导\n"
                "2. 完整性：是否覆盖了用户问题的各个方面\n"
                "3. 实用性：对一线销售人员是否有直接可用的指导价值\n"
                "4. 具体性：是给出了具体的策略和话术，还是泛泛而谈\n"
                "5. 语气：是否符合销售教练的专业角色\n\n"
                "如果两个回答质量相当，请选你觉得略微更好的那个。"
            ),
            evaluation_params=[
                SingleTurnParams.INPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
            ],
            model=model,
        )
        metric.measure(arena_tc)

        return {
            "winner": metric.winner,
            "reason": metric.reason,
            "score_a": getattr(metric, "score_a", None),
            "score_b": getattr(metric, "score_b", None),
            "error": None,
        }
    except Exception as e:
        return {"winner": None, "reason": "", "error": str(e)}


# ── 导出 ──────────────────────────────────────────────────────────

__all__ = [
    "get_judge_model",
    "set_global_judge_model",
    "get_metrics_for_question",
    "AnswerRecallMetric",
    "run_arena_comparison",
    "make_correctness_metric",
    "make_completeness_metric",
    "make_faithfulness_metric",
    "make_answer_relevancy_metric",
    "make_hallucination_metric",
]
