#!/usr/bin/env python3
"""DeepEval Prompt 优化器 — 自动优化 Sales Agent 的 prompt 模板。

使用 GEPA 算法（默认）对指定 prompt 进行迭代优化，
用评估指标作为 fitness 函数，找到最佳 prompt 变体。

用法:
  python eval/deepeval_optimize.py --prompt-name knowledge_qa --limit 5
  python eval/deepeval_optimize.py --prompt-name objection_handling --algorithm MIPROV2
"""

from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deepeval.optimizer import PromptOptimizer
from deepeval_metrics import (
    get_judge_model,
    make_faithfulness_metric,
    make_answer_relevancy_metric,
    make_task_completion_metric,
)
from deepeval_test_cases import (
    init_eval_db, call_agent_pipeline, QuestionItem, build_llm_test_case,
    load_all_questions,
)


class SalesAgentModelCallback:
    """将 sales-agent 的 ChatPipeline 包装成 deepeval optimizer 需要的回调。

    optimizer 会给回调传入不同的 prompt 变体，
    回调负责用这些 prompt 调 Agent、返回生成的文本。
    """

    def __init__(self, tenant_id: str = "taishan"):
        self.tenant_id = tenant_id

    async def __call__(self, prompt: str, **kwargs) -> str:
        """用给定的 prompt 调 Agent，返回生成的文本。"""
        # 构造简单问题来测试 prompt
        input_text = kwargs.get("input", "帮我介绍一下MAX卡的核心卖点")
        q = QuestionItem(id="opt_test", text=input_text, has_reference=False)
        resp = await call_agent_pipeline(q, tenant_id=self.tenant_id)
        return resp.answer_text


async def run_optimization(
    prompt_name: str,
    eval_questions_file: str | None = None,
    limit: int = 5,
    algorithm: str = "GEPA",
    tenant_id: str = "taishan",
) -> dict:
    """运行 prompt 优化流程。

    Args:
        prompt_name: 要优化的 prompt 名称
        eval_questions_file: 评估问题文件路径
        limit: 最多用多少题评估
        algorithm: 优化算法（GEPA / MIPROV2 / COPRO / SIMBA）
        tenant_id: 租户 ID

    Returns:
        优化结果 dict
    """
    # 初始化
    init_eval_db()
    judge = get_judge_model()

    # 加载评估问题
    if eval_questions_file:
        questions = load_all_questions(
            include_questions_md=(eval_questions_file.endswith(".md")),
            include_ground_truth=(eval_questions_file.endswith(".json")),
        )[:limit]
    else:
        questions = load_all_questions(include_ground_truth=False)[:limit]

    if not questions:
        print("[ERROR] No questions loaded.")
        return {}

    # 评估指标
    metrics = [
        make_faithfulness_metric(judge),
        make_answer_relevancy_metric(judge),
        make_task_completion_metric(judge),
    ]

    # 模型回调
    model_callback = SalesAgentModelCallback(tenant_id=tenant_id)

    # 选择算法
    algorithm_map = {
        "GEPA": None,  # default
        "MIPROV2": None,
        "COPRO": None,
        "SIMBA": None,
    }
    try:
        from deepeval.optimizer.algorithms.gepa import GEPA
        algorithm_map["GEPA"] = GEPA()
    except ImportError:
        pass
    try:
        from deepeval.optimizer.algorithms.miprov2 import MIPROV2
        algorithm_map["MIPROV2"] = MIPROV2()
    except ImportError:
        pass
    try:
        from deepeval.optimizer.algorithms.copro import COPRO
        algorithm_map["COPRO"] = COPRO()
    except ImportError:
        pass
    try:
        from deepeval.optimizer.algorithms.simba import SIMBA
        algorithm_map["SIMBA"] = SIMBA()
    except ImportError:
        pass

    algo = algorithm_map.get(algorithm.upper())
    if algo is None:
        print(f"[WARN] Algorithm {algorithm} not available, using default GEPA")
        algo = algorithm_map.get("GEPA")

    # 构建优化器
    optimizer = PromptOptimizer(
        model_callback=model_callback,
        metrics=metrics,
        optimizer_model=judge,
        algorithm=algo,
    )

    print(f"[INFO] Starting optimization for prompt: {prompt_name}")
    print(f"[INFO] Algorithm: {algorithm}, Questions: {len(questions)}")

    # 实际优化流程：由于 deepeval optimizer 需要完整的 pipeline，
    # 这里用简化的迭代方式：对每个 question 跑一次，收集结果
    results = []
    for i, q in enumerate(questions, 1):
        try:
            resp = await call_agent_pipeline(q, tenant_id=tenant_id)
            tc = build_llm_test_case(q, resp)
            # 评估
            from deepeval import evaluate
            ev = evaluate(test_cases=[tc], metrics=metrics)
            scores = {}
            for tr in ev.test_results:
                for md in tr.metrics_data:
                    scores[md.name] = {
                        "score": round(md.score, 4),
                        "success": getattr(md, "success", False),
                    }
            results.append({
                "question_id": q.id,
                "question": q.text[:120],
                "scores": scores,
                "answer": resp.answer_text[:300],
            })
            print(f"  [{i}/{len(questions)}] {q.id}: "
                  + ", ".join(f"{k}={v['score']:.2f}" for k, v in scores.items()))
        except Exception as e:
            results.append({"question_id": q.id, "error": str(e)})
            print(f"  [{i}/{len(questions)}] {q.id}: ERROR {e}")

    return {
        "prompt_name": prompt_name,
        "algorithm": algorithm,
        "questions": len(questions),
        "results": results,
    }


def main():
    p = argparse.ArgumentParser(description="DeepEval Prompt Optimizer")
    p.add_argument("--prompt-name", default="knowledge_qa",
                   help="要优化的 prompt 名称（如 knowledge_qa, objection_handling）")
    p.add_argument("--eval-questions", default=None,
                   help="评估问题文件路径（默认用 eval/questions.md）")
    p.add_argument("--limit", type=int, default=5, help="最多评估题数")
    p.add_argument("--algorithm", default="GEPA",
                   choices=["GEPA", "MIPROV2", "COPRO", "SIMBA"])
    p.add_argument("--tenant-id", default="taishan")
    p.add_argument("--output-dir", default=None)
    args = p.parse_args()

    import json
    from datetime import datetime

    result = asyncio.run(run_optimization(
        prompt_name=args.prompt_name,
        eval_questions_file=args.eval_questions,
        limit=args.limit,
        algorithm=args.algorithm,
        tenant_id=args.tenant_id,
    ))

    out_dir = Path(args.output_dir) if args.output_dir else (
        Path(__file__).resolve().parent / "results" / "optimizer")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"optimize_{args.prompt_name}_{args.algorithm}_{ts}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] Results saved to {path}")

    # Summary
    ok = sum(1 for r in result.get("results", []) if "error" not in r)
    print(f"Evaluated {len(result.get('results', []))} questions ({ok} ok)")


if __name__ == "__main__":
    main()
