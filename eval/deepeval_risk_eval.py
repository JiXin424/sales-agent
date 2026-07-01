"""DeepEval 风险评估指标 — 验证 Agent 的风险检测行为是否正确。

风险检测与内容质量评估不同：
- 内容指标（Faithfulness 等）：回答内容好不好
- 风险指标：该拦截的拦截了没，不该拦截的误杀了没

核心指标：
  Recall (检出率)    = 被正确拦截的高风险请求 / 所有应被拦截的请求
  Precision (准确率) = 被正确拦截的请求 / 所有被拦截的请求
  F1                 = 调和平均
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from deepeval.metrics import BaseMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from deepeval.metrics.utils import initialize_model

from deepeval_test_cases import QuestionItem, call_agent_pipeline


def load_risk_test_questions(path: str | Path | None = None) -> list[dict]:
    """加载风险测试问题集。

    Returns:
        [{"id": "risk_001", "text": "...", "expected_risk_level": "high",
          "expected_action": "block", "should_block": true, ...}, ...]
    """
    if path is None:
        path = Path(__file__).resolve().parent / "risk_test_questions.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def run_risk_evaluation(
    tenant_id: str = "taishan",
    questions: list[dict] | None = None,
) -> dict:
    """对一组风险问题运行 Agent，收集风险检测结果。

    Returns:
        {
            "total": 15,
            "should_block": 10,   # 应该被拦截的
            "should_allow": 5,    # 应该放行的
            "true_positive": 8,   # 正确拦截
            "false_negative": 2,  # 漏拦（危险）
            "true_negative": 4,   # 正确放行
            "false_positive": 1,  # 误拦
            "recall": 0.80,
            "precision": 0.89,
            "f1": 0.84,
            "details": [...]
        }
    """
    if questions is None:
        questions = load_risk_test_questions()

    details: list[dict] = []
    tp = tn = fp = fn = 0

    for q in questions:
        item = QuestionItem(
            id=q["id"],
            text=q["text"],
            category=q.get("category", ""),
            has_reference=False,
        )
        resp = await call_agent_pipeline(item, tenant_id=tenant_id)

        detected = resp.risk_level != "none" and resp.risk_level != ""
        blocked = resp.risk_level in ("high", "medium")  # 有实质风险动作

        should_block = q.get("should_block", False)
        expected_level = q.get("expected_risk_level", "none")
        expected_action = q.get("expected_action", "allow")

        # 分类
        if should_block and blocked:
            tp += 1; classification = "tp"
        elif should_block and not blocked:
            fn += 1; classification = "fn"
        elif not should_block and blocked:
            fp += 1; classification = "fp"
        elif not should_block and not blocked:
            tn += 1; classification = "tn"

        detail = {
            "id": q["id"],
            "question": q["text"][:150],
            "category": q.get("category", ""),
            "expected_level": expected_level,
            "expected_action": expected_action,
            "should_block": should_block,
            "actual_level": resp.risk_level,
            "actual_flags": resp.risk_flags,
            "actual_error": resp.error,
            "classification": classification,
            "passed": (should_block and blocked) or (not should_block and not blocked),
        }
        details.append(detail)

    total = len(questions)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "total": total,
        "should_block": tp + fn,
        "should_allow": tn + fp,
        "true_positive": tp,
        "false_negative": fn,
        "true_negative": tn,
        "false_positive": fp,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
        "details": details,
    }


def format_risk_report(result: dict) -> str:
    """格式化风险评估报告。"""
    return (
        f"风险评估报告\n"
        f"──────────────\n"
        f"总测试数: {result['total']}\n"
        f"  应拦截: {result['should_block']} 题\n"
        f"  应放行: {result['should_allow']} 题\n\n"
        f"检出率 (Recall):    {result['recall']:.1%}  "
        f"({result['true_positive']}/{result['should_block']} 正确拦截)\n"
        f"准确率 (Precision): {result['precision']:.1%}  "
        f"({result['false_positive']} 次误拦)\n"
        f"F1 Score:          {result['f1']:.1%}\n\n"
        f"⚠️  漏拦 (False Negatives): {result['false_negative']}  "
        f"← 最危险的，应降为 0\n"
        f"✅ 误拦 (False Positives): {result['false_positive']}  "
        f"← 影响用户体验，应尽量少"
    )


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    import argparse, asyncio, sys, json
    from datetime import datetime
    from deepeval_test_cases import init_eval_db

    p = argparse.ArgumentParser(
        description="Sales Agent 风险检测评估")
    p.add_argument("--tenant-id", default="taishan")
    p.add_argument("--questions-file", default=None,
                   help="风险问题 JSON 文件（默认用 eval/risk_test_questions.json）")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--json", action="store_true",
                   help="输出 JSON 到 stdout")
    args = p.parse_args()

    init_eval_db()
    questions = load_risk_test_questions(args.questions_file)
    print(f"[INFO] Loaded {len(questions)} risk test questions")

    result = asyncio.run(run_risk_evaluation(
        tenant_id=args.tenant_id, questions=questions))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print()
        print(format_risk_report(result))
        print()
        # 列出失败项
        failures = [d for d in result["details"] if not d["passed"]]
        if failures:
            print("失败详情:")
            for d in failures:
                tag = "漏拦⚠️" if d["classification"] == "fn" else "误拦✅"
                print(f"  [{d['id']}] {tag} | {d['category']}")
                print(f"    Q: {d['question']}")
                print(f"    Expected: level={d['expected_level']}, "
                      f"action={d['expected_action']}")
                print(f"    Actual:   level={d['actual_level']}, "
                      f"flags={d['actual_flags']}")
                print()
        else:
            print("✅ 所有风险检测用例通过！")

    # 保存报告
    out_dir = Path(args.output_dir) if args.output_dir else (
        Path(__file__).resolve().parent / "results" / "risk")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pth = out_dir / f"risk_eval_{ts}.json"
    pth.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n[OK] Report saved to {pth}")


if __name__ == "__main__":
    main()


__all__ = [
    "load_risk_test_questions",
    "run_risk_evaluation",
    "format_risk_report",
]
