"""
收敛判断节点：决定是继续迭代还是终止。
"""

from __future__ import annotations

from eval.optimizer.state import ActionType, OptimizerState, RoundMetrics


# ── 收敛阈值 ──────────────────────────────────────────────────────────

MIN_IMPROVEMENT = 0.05   # 连续两轮 pass_rate 提升不足 5% 视为停滞
MAX_STALL_ROUNDS = 3     # 最多停滞轮次
IMPROVEMENT_FIELD = "avg_contextual_recall"  # 主导改善信号


def judge_convergence(
    state: OptimizerState,
    metrics: RoundMetrics,
) -> tuple[bool, str]:
    """判断是否收敛。

    Returns:
        (is_converged, reason)
    """
    current_round = state["round"]
    max_rounds = state["max_rounds"]
    target = state["target_pass_rate"]
    history = state.get("history", [])
    best_pass = state.get("best_pass_rate", 0.0)
    stall_count = state.get("rounds_without_improvement", 0)

    # 1) 超过最大轮次
    if current_round >= max_rounds:
        return True, f"达到最大迭代轮次 {max_rounds}"

    # 2) pass_rate 达标
    if metrics.pass_rate >= target:
        return True, f"pass_rate ({metrics.pass_rate:.1%}) 达到目标 ({target:.1%})"

    # 3) 全部满分（完美）
    if metrics.pass_rate >= 1.0:
        return True, "所有题目通过，完美收敛"

    # 4) 停滞检测
    if metrics.pass_rate <= best_pass:
        stall_count += 1
    else:
        stall_count = 0  # 有进步，重置停滞计数器

    # 更新 best_pass_rate
    if metrics.pass_rate > best_pass:
        best_pass = metrics.pass_rate

    state["rounds_without_improvement"] = stall_count
    state["best_pass_rate"] = best_pass

    if stall_count >= MAX_STALL_ROUNDS:
        return True, f"连续 {MAX_STALL_ROUNDS} 轮无明显改善（best={best_pass:.1%}）"

    # 5) 检索指标是否还有改善空间
    if history and len(history) >= 2:
        prev = history[-1].metrics_before
        prev2 = history[-2].metrics_before if len(history) >= 2 else prev
        if prev:
            delta_recall = abs(
                (metrics.avg_contextual_recall or 0) - (prev.avg_contextual_recall or 0)
            )
            delta_relevancy = abs(
                (metrics.avg_contextual_relevancy or 0) - (prev.avg_contextual_relevancy or 0)
            )
            if delta_recall < MIN_IMPROVEMENT and delta_relevancy < MIN_IMPROVEMENT:
                stall_count += 1

    return False, ""
