#!/usr/bin/env python3
"""
迭代优化器 CLI —— 从知识库出题 → 评估 → 诊断 → 调优 → 闭环迭代。

用法:
    python -m eval.optimizer.runner \
        --tenant-id taishankaifa2 \
        --docs-dir data/taishankaifa2/documents \
        --max-rounds 5 \
        --target-pass-rate 0.8
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# 确保 eval 可 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.optimizer.graph import build_graph
from eval.optimizer.state import OptimizerState

logger = logging.getLogger(__name__)


def _load_dotenv(repo_root: Path) -> None:
    """加载项目根 .env 到 os.environ。"""
    env_file = repo_root / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v


def main() -> None:
    p = argparse.ArgumentParser(
        description="迭代优化器：出题 → 评估 → 诊断 → 调优 → 闭环迭代"
    )
    p.add_argument("--tenant-id", required=True, help="租户 ID")
    p.add_argument("--docs-dir", required=True, help="知识库文档目录")
    p.add_argument("--output-dir", default="eval/datasets/optimizer",
                   help="输出根目录（默认 eval/datasets/optimizer/）")
    p.add_argument("--max-rounds", type=int, default=5,
                   help="最大迭代轮次（默认 5）")
    p.add_argument("--target-pass-rate", type=float, default=0.8,
                   help="目标 pass_rate（默认 0.8）")
    p.add_argument("--eval-limit", type=int, default=20,
                   help="每轮最多评估题数（默认 20）")
    p.add_argument("--no-checkpoint", action="store_true",
                   help="禁用 checkpoint，从头开始")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="详细日志")

    args = p.parse_args()

    # ── 初始化 ────────────────────────────────────────────────────
    repo_root = Path(__file__).resolve().parent.parent.parent
    _load_dotenv(repo_root)
    os.environ["TENANT_ID"] = args.tenant_id

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── 初始状态 ──────────────────────────────────────────────────
    initial_state: OptimizerState = {
        "tenant_id": args.tenant_id,
        "docs_dir": os.path.abspath(args.docs_dir),
        "output_dir": os.path.abspath(args.output_dir),
        "golden_file": "",
        "max_rounds": args.max_rounds,
        "target_pass_rate": args.target_pass_rate,
        "eval_limit": args.eval_limit,
        "round": 1,
        "history": [],
        "retrieval_config": {
            "top_k": 5,
            "chunk_size": 700,
            "chunk_overlap": 120,
        },
        "convergence_reason": "",
        "best_pass_rate": 0.0,
        "rounds_without_improvement": 0,
    }

    # ── 构建图 ────────────────────────────────────────────────────
    graph = build_graph(checkpoint=not args.no_checkpoint)

    logger.info("=" * 60)
    logger.info("迭代优化器启动")
    logger.info("  租户: %s", args.tenant_id)
    logger.info("  文档目录: %s", args.docs_dir)
    logger.info("  最大轮次: %d", args.max_rounds)
    logger.info("  目标 pass_rate: %.0f%%", args.target_pass_rate * 100)
    logger.info("  初始检索配置: %s", initial_state["retrieval_config"])
    logger.info("=" * 60)

    # ── 运行 ──────────────────────────────────────────────────────
    config = {"configurable": {"thread_id": f"optimizer-{args.tenant_id}"}}

    async def _run() -> None:
        final_state = None
        async for step in graph.astream(initial_state, config):
            node_name = list(step.keys())[0]
            logger.info("─" * 40)
            logger.info("Node completed: %s", node_name)

            # 每轮评估完后打印关键指标
            if node_name == "evaluate":
                state_data = step[node_name]
                metrics = state_data.get("_metrics")
                if metrics:
                    logger.info(
                        "  pass_rate=%.1f%%  recall=%.2f  relevancy=%.2f  "
                        "faithfulness=%.2f  correctness=%.2f",
                        metrics.pass_rate * 100,
                        metrics.avg_contextual_recall,
                        metrics.avg_contextual_relevancy,
                        metrics.avg_faithfulness,
                        metrics.avg_correctness,
                    )
            elif node_name == "triage":
                state_data = step[node_name]
                diag = state_data.get("_diagnosis")
                if diag:
                    logger.info("  action=%s  issue=%s", diag.action.value, diag.primary_issue)
            elif node_name == "judge":
                state_data = step[node_name]
                reason = state_data.get("convergence_reason", "")
                if reason:
                    logger.info("  ✅ 收敛: %s", reason)

            final_state = step

        # ── 最终报告 ───────────────────────────────────────────────
        if final_state:
            state_key = list(final_state.keys())[-1]
            s = final_state[state_key]
            history = s.get("history", [])
            logger.info("=" * 60)
            logger.info("迭代完成！")
            logger.info("  总轮次: %d", s.get("round", 0))
            logger.info("  历史动作: %d 条", len(history))
            logger.info("  最终检索配置: %s", s.get("retrieval_config", {}))
            logger.info("  收敛原因: %s", s.get("convergence_reason", "未收敛"))
            logger.info("  最佳 pass_rate: %.1f%%", s.get("best_pass_rate", 0.0) * 100)
            for i, action in enumerate(history, 1):
                logger.info("  轮次 %d: %s → %s",
                            action.round,
                            action.diagnosis.action.value if action.diagnosis else "?",
                            action.tuning.reasoning[:100] if action.tuning else "(no tuning)")

        logger.info("输出目录: %s", args.output_dir)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
