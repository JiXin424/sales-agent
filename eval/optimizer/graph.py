"""
LangGraph 图定义：迭代优化器主图。

节点：
  synthesize → evaluate → triage → [tune_retrieval / fix_docs] → judge → loop/end
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from eval.optimizer.state import (
    ActionType,
    OptimizerState,
    RoundAction,
    RoundMetrics,
)
from eval.optimizer.tools.synthesize import run_synthesize
from eval.optimizer.tools.evaluate import run_evaluate
from eval.optimizer.tools.triage import diagnose
from eval.optimizer.tools.tune_retrieval import tune_retrieval, apply_tuning
from eval.optimizer.tools.evaluate import _resolve_knowledge_engine
from eval.optimizer.tools.judge import judge_convergence

logger = logging.getLogger(__name__)


# ── 节点函数 ──────────────────────────────────────────────────────────


async def node_synthesize(state: OptimizerState) -> dict:
    """出题节点：生成 Golden 测试数据（首轮），后续轮复用已有文件。"""
    round_num = state["round"]
    output_dir = str(Path(state["output_dir"]) / f"round_{round_num:02d}")

    # 后续轮复用 Round 1 的 goldens，避免重复合成（DeepSeek JSON 不稳定）
    if round_num > 1:
        golden_file = str(Path(state["output_dir"]) / "round_01" / "goldens.json")
        if Path(golden_file).is_file():
            logger.info("[Round %d] Reusing goldens from %s", round_num, golden_file)
            return {"golden_file": golden_file}

    logger.info("[Round %d] Synthesizing goldens from %s", round_num, state["docs_dir"])
    json_path, count = run_synthesize(
        docs_dir=state["docs_dir"],
        output_dir=output_dir,
        max_goldens=0,  # 不限总量，让 limit-per-doc 控制
        limit_per_doc=3,  # 每轮每篇文档 3 题
    )
    logger.info("[Round %d] Generated %d goldens → %s", round_num, count, json_path)
    return {"golden_file": json_path}


async def node_evaluate(state: OptimizerState) -> dict:
    """评估节点：对 Golden 运行评测。"""
    logger.info("[Round %d] Evaluating golden file: %s", state["round"], state["golden_file"])
    output_dir = str(Path(state["output_dir"]) / f"round_{state['round']:02d}" / "results")
    metrics, _ = run_evaluate(
        tenant_id=state["tenant_id"],
        golden_file=state["golden_file"],
        output_dir=output_dir,
        limit=state.get("eval_limit", 0),
    )
    metrics.round = state["round"]
    logger.info(
        "[Round %d] Eval complete: pass_rate=%.1f%%, recall=%.2f, relevancy=%.2f",
        state["round"],
        metrics.pass_rate * 100,
        metrics.avg_contextual_recall,
        metrics.avg_contextual_relevancy,
    )
    return {"_metrics": metrics}


async def node_triage(state: OptimizerState) -> dict:
    """诊断节点：分析指标，决定调优方向。"""
    metrics: RoundMetrics = state["_metrics"]
    diag = diagnose(state, metrics)
    logger.info(
        "[Round %d] Diagnosis: issue=%s action=%s low_count=%d",
        state["round"], diag.primary_issue, diag.action.value, diag.low_score_count,
    )
    logger.info("  Reasoning: %s", diag.reasoning)
    return {"_diagnosis": diag}


async def node_tune_retrieval(state: OptimizerState) -> dict:
    """Tool A：LLM 调检索配置。"""
    diag: "Diagnosis" = state["_diagnosis"]
    metrics: "RoundMetrics" = state["_metrics"]
    current_config = state.get("retrieval_config", {})

    # 从 metrics 中筛选低分题目（检索指标低 或 生成质量低）
    low_cases = [
        snap for snap in metrics.per_question
        if not snap.error and (
            min(snap.contextual_recall, snap.contextual_relevancy) < 0.3
            or snap.correctness < 0.4
            or snap.faithfulness < 0.5
        )
    ]
    # 按综合质量排序：correctness + faithfulness 越低越靠前
    low_cases.sort(key=lambda s: s.correctness + s.faithfulness)
    low_cases = low_cases[:10]

    tuning = await tune_retrieval(
        tenant_id=state["tenant_id"],
        diagnosis=diag,
        low_score_cases=low_cases,
        current_config=current_config,
        engine_type=_resolve_knowledge_engine(state["tenant_id"]),
    )

    logger.info("[Round %d] Tuning suggestion: %s", state["round"], tuning)

    # 应用配置（检查是否有任何变更）
    has_change = any([
        tuning.top_k, tuning.chunk_size, tuning.chunk_overlap,
        tuning.entity_limit, tuning.facts_per_entity,
        tuning.max_entities_for_prompt, tuning.max_facts_for_prompt,
        tuning.vector_fallback_top_k,
    ])
    if has_change:
        await apply_tuning(state["tenant_id"], tuning)

    # 更新 state（RAG + Ontology 全量同步）
    new_config = dict(current_config)
    for key in ("top_k", "chunk_size", "chunk_overlap",
                "entity_limit", "facts_per_entity",
                "max_entities_for_prompt", "max_facts_for_prompt",
                "vector_fallback_top_k"):
        val = getattr(tuning, key, None)
        if val is not None:
            new_config[key] = val

    action = RoundAction(
        round=state["round"],
        diagnosis=diag,
        tuning=tuning,
        metrics_before=metrics,
    )
    history = list(state.get("history", []))
    history.append(action)

    return {
        "retrieval_config": new_config,
        "history": history,
    }


async def node_fix_docs(state: OptimizerState) -> dict:
    """Tool B（Phase 2 预留）：分析并补写知识库文档。

    当前为桩实现，只打印诊断信息。
    """
    diag = state["_diagnosis"]
    logger.warning(
        "[Round %d] FIX_DOCS triggered (Phase 2 stub). "
        "Would fix docs based on: %s",
        state["round"], diag.reasoning,
    )

    action = RoundAction(
        round=state["round"],
        diagnosis=diag,
        doc_changes=[f"[STUB] Would fix: {diag.reasoning}"],
    )
    history = list(state.get("history", []))
    history.append(action)

    return {"history": history}


async def node_judge(state: OptimizerState) -> dict:
    """收敛判断节点。"""
    metrics: RoundMetrics = state["_metrics"]
    is_converged, reason = judge_convergence(state, metrics)

    if is_converged:
        logger.info("[Round %d] CONVERGED: %s", state["round"], reason)
        return {"convergence_reason": reason}
    else:
        logger.info("[Round %d] Continue to next round...", state["round"])
        return {"convergence_reason": "", "round": state["round"] + 1}


# ── 路由函数 ──────────────────────────────────────────────────────────


def route_after_triage(state: OptimizerState) -> str:
    """根据诊断结果路由到不同 Tool。"""
    diag = state["_diagnosis"]
    if diag.action == ActionType.TUNE_RETRIEVAL:
        return "tune_retrieval"
    elif diag.action == ActionType.FIX_DOCS:
        return "fix_docs"
    else:
        return "judge"


def route_after_tool(state: OptimizerState) -> str:
    """Tool 执行后进入 judge。"""
    return "judge"


def route_after_judge(state: OptimizerState) -> str:
    """收敛判断后：继续循环 or 结束。"""
    if state.get("convergence_reason"):
        return END
    return "synthesize"


# ── 构建图 ───────────────────────────────────────────────────────────


def build_graph(checkpoint: bool = True) -> StateGraph:
    """构建迭代优化器 LangGraph 图。

    Args:
        checkpoint: 是否启用 checkpoint（断点续传）

    Returns:
        编译后的 StateGraph
    """
    builder = StateGraph(OptimizerState)

    # 注册节点
    builder.add_node("synthesize", node_synthesize)
    builder.add_node("evaluate", node_evaluate)
    builder.add_node("triage", node_triage)
    builder.add_node("tune_retrieval", node_tune_retrieval)
    builder.add_node("fix_docs", node_fix_docs)
    builder.add_node("judge", node_judge)

    # 设置入口
    builder.set_entry_point("synthesize")

    # 连边
    builder.add_edge("synthesize", "evaluate")
    builder.add_edge("evaluate", "triage")

    builder.add_conditional_edges("triage", route_after_triage, {
        "tune_retrieval": "tune_retrieval",
        "fix_docs": "fix_docs",
        "judge": "judge",
    })

    builder.add_edge("tune_retrieval", "judge")
    builder.add_edge("fix_docs", "judge")

    builder.add_conditional_edges("judge", route_after_judge, {
        "synthesize": "synthesize",
        END: END,
    })

    # 编译
    if checkpoint:
        memory = MemorySaver()
        return builder.compile(checkpointer=memory)
    return builder.compile()
