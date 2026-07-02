"""LangGraph 状态定义 + Pydantic 诊断/行动计划模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


# ── 动作枚举 ──────────────────────────────────────────────────────────


class ActionType(str, Enum):
    TUNE_RETRIEVAL = "tune_retrieval"   # 调检索配置
    FIX_DOCS = "fix_docs"               # 补/改知识库文档
    CONVERGE = "converge"               # 已收敛，停止迭代


# ── Pydantic 诊断/计划模型（供 LLM structured output） ──────────────


@dataclass
class MetricSnapshot:
    """单题维度指标快照。"""
    question_id: str
    question: str
    source_file: str = ""
    answer: str = ""
    reference: str = ""
    # 核心检索指标
    contextual_relevancy: float = 0.0
    contextual_recall: float = 0.0
    contextual_precision: float = 0.0
    # 生成指标
    faithfulness: float = 0.0
    correctness: float = 0.0
    completeness: float = 0.0
    answer_relevancy: float = 0.0
    task_completion: float = 0.0
    answer_recall: float = 0.0
    # 元信息
    error: str = ""


@dataclass
class RoundMetrics:
    """单轮评估的汇总指标。"""
    round: int
    total: int = 0
    success: int = 0
    pass_rate: float = 0.0
    avg_contextual_recall: float = 0.0
    avg_contextual_relevancy: float = 0.0
    avg_contextual_precision: float = 0.0
    avg_faithfulness: float = 0.0
    avg_correctness: float = 0.0
    avg_completeness: float = 0.0
    avg_answer_relevancy: float = 0.0
    avg_answer_recall: float = 0.0
    avg_task_completion: float = 0.0
    avg_latency_ms: float = 0.0
    per_question: list[MetricSnapshot] = field(default_factory=list)

    @classmethod
    def from_eval_summary(cls, round_num: int, summary: dict[str, Any],
                          per_question: list[MetricSnapshot]) -> "RoundMetrics":
        scores = summary.get("scores", {})
        return cls(
            round=round_num,
            total=summary.get("total", 0),
            success=summary.get("success", 0),
            pass_rate=summary.get("pass_rate", 0.0),
            avg_contextual_recall=scores.get("Contextual Recall", 0.0),
            avg_contextual_relevancy=scores.get("Contextual Relevancy", 0.0),
            avg_contextual_precision=scores.get("Contextual Precision", 0.0),
            avg_faithfulness=scores.get("Faithfulness", 0.0),
            avg_correctness=scores.get("正确性 (Correctness) [GEval]", 0.0),
            avg_completeness=scores.get("完整性 (Completeness) [GEval]", 0.0),
            avg_answer_relevancy=scores.get("Answer Relevancy", 0.0),
            avg_answer_recall=scores.get("AnswerRecall (回答召回率)", 0.0),
            avg_task_completion=scores.get("Task Completion", 0.0),
            avg_latency_ms=summary.get("latency_ms", 0.0),
            per_question=per_question,
        )


@dataclass
class Diagnosis:
    """单轮评估的诊断结果（LLM 生成）。"""
    primary_issue: str          # "retrieval" | "document" | "mixed"
    reasoning: str              # 诊断推理过程
    action: ActionType          # 推荐动作
    low_score_count: int = 0    # 低分题目数
    sample_questions: list[str] = field(default_factory=list)  # 代表性低分题目 ID


@dataclass
class RetrievalTuning:
    """检索配置调优建议（Tool A 输出）。"""
    # 调整建议
    top_k: int | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    # 推理
    reasoning: str = ""
    expected_improvement: str = ""


@dataclass
class RoundAction:
    """单轮执行的动作记录。"""
    round: int
    diagnosis: Diagnosis | None = None
    tuning: RetrievalTuning | None = None
    metrics_before: RoundMetrics | None = None
    metrics_after: RoundMetrics | None = None
    doc_changes: list[str] = field(default_factory=list)  # Phase 2


# ── LangGraph State ──────────────────────────────────────────────────


class OptimizerState(TypedDict):
    """LangGraph 图的状态。

    各节点读写这个 dict，LangGraph 负责持久化和恢复。
    """
    # ── 配置 ──
    tenant_id: str
    docs_dir: str
    output_dir: str
    golden_file: str          # 每次 synthesize 的输出路径
    max_rounds: int
    target_pass_rate: float
    eval_limit: int           # 每轮跑多少题

    # ── 运行时 ──
    round: int                # 当前轮次
    history: list[RoundAction]  # 历史动作

    # ── 检索配置（可调参数） ──
    retrieval_config: dict[str, Any]  # {top_k, chunk_size, chunk_overlap}

    # ── 收敛信号 ──
    convergence_reason: str   # 空 = 未收敛
    best_pass_rate: float     # 历史最佳
    rounds_without_improvement: int
