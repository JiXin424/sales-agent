"""
诊断节点：分析逐题指标，决定走 Tool A（调检索）还是 Tool B（补文档）。

分流逻辑（Phase 1 简化版）：

    ContextualRecall < 0.3 AND ContextualRelevancy < 0.3
    → 检索既捞不准也捞不全 → TUNE_RETRIEVAL  (Tool A)

    ContextualRecall < 0.3 AND ContextualRelevancy >= 0.5
    → 检索精准但信息不在库里 → FIX_DOCS  (Tool B，Phase 2)

    连续 3 轮无改善 → 尝试 FIX_DOCS
"""

from __future__ import annotations

from eval.optimizer.state import (
    ActionType,
    Diagnosis,
    MetricSnapshot,
    OptimizerState,
    RoundMetrics,
)


# ── 阈值常量 ──────────────────────────────────────────────────────────

RECALL_LOW = 0.3
RELEVANCY_LOW = 0.3
RELEVANCY_OK = 0.5
# 整体 pass_rate 到多少算"检索指标不够好"
STALL_THRESHOLD = 0.05  # 连续两轮改善不足 5%


def diagnose(state: OptimizerState, metrics: RoundMetrics) -> Diagnosis:
    """根据逐题指标生成诊断结果。

    直接规则匹配，不调 LLM——因为分流逻辑是确定性的。
    LLM 只在 Tool A/B 内部做具体的推理。
    """
    low_score_cases = _find_low_score_cases(metrics.per_question)

    if not low_score_cases:
        return Diagnosis(
            primary_issue="none",
            reasoning="所有题目指标均正常，无需干预。",
            action=ActionType.CONVERGE,
            low_score_count=0,
        )

    # 统计低分题目的指标分布
    retrieval_broken = 0   # 检索全坏
    retrieval_ok_doc_bad = 0  # 检索 OK 但缺信息

    for snap in low_score_cases:
        if snap.contextual_relevancy < RELEVANCY_LOW and snap.contextual_recall < RECALL_LOW:
            retrieval_broken += 1
        elif snap.contextual_recall < RECALL_LOW and snap.contextual_relevancy >= RELEVANCY_OK:
            retrieval_ok_doc_bad += 1

    total_low = len(low_score_cases)

    # 检查是否连续多轮无改善（触发 doc fix）
    stall = state.get("rounds_without_improvement", 0) >= 3

    # ── 决策 ──
    if retrieval_broken > total_low * 0.5:
        # 大多数低分题是检索问题
        action = ActionType.FIX_DOCS if stall else ActionType.TUNE_RETRIEVAL
        primary = "retrieval"
        reasoning = (
            f"{retrieval_broken}/{total_low} 道低分题的 ContextualRecall 和 "
            f"ContextualRelevancy 同时偏低，说明检索既捞不准也捞不全。"
            + (" 但已连续多轮无改善，建议检查文档内容。" if stall else " 建议调检索配置。")
        )
    elif retrieval_ok_doc_bad > total_low * 0.3:
        action = ActionType.FIX_DOCS
        primary = "document"
        reasoning = (
            f"{retrieval_ok_doc_bad}/{total_low} 道低分题检索精准但召回不足，"
            f"说明关键信息未在知识库中或组织方式不利于检索。建议补写文档。"
        )
    else:
        # 混合情况，先调检索
        action = ActionType.FIX_DOCS if stall else ActionType.TUNE_RETRIEVAL
        primary = "mixed"
        reasoning = (
            f"低分题目中 {retrieval_broken} 道检索全坏、{retrieval_ok_doc_bad} 道缺信息。"
            + (" 多轮无改善，转向文档优化。" if stall else " 优先调检索配置，第二轮再评估文档。")
        )

    sample_ids = [s.question_id for s in low_score_cases[:5]]

    return Diagnosis(
        primary_issue=primary,
        reasoning=reasoning,
        action=action,
        low_score_count=total_low,
        sample_questions=sample_ids,
    )


def _find_low_score_cases(per_question: list[MetricSnapshot]) -> list[MetricSnapshot]:
    """找出任一检索指标低于阈值的题目。"""
    low: list[MetricSnapshot] = []
    for snap in per_question:
        if snap.error:
            continue  # 跳过报错的题目
        worst = min(snap.contextual_recall, snap.contextual_relevancy)
        if worst < RECALL_LOW:
            low.append(snap)
    # 按指标从低到高排
    low.sort(key=lambda s: min(s.contextual_recall, s.contextual_relevancy))
    return low
