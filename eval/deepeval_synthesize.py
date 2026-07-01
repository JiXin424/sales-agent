#!/usr/bin/env python3
"""从产品文档自动生成 DeepEval Golden 测试数据。"""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="从产品文档自动生成 DeepEval Golden 测试数据"
    )
    parser.add_argument(
        "--docs-dir",
        required=True,
        help="产品文档目录（递归扫描 .md/.txt/.pdf）",
    )
    parser.add_argument(
        "--output",
        default="eval/datasets/",
        help="输出目录（默认 eval/datasets/）",
    )
    parser.add_argument(
        "--max-goldens",
        type=int,
        default=20,
        help="最大生成的 Golden 总数（默认 20）",
    )
    parser.add_argument(
        "--limit-per-doc",
        type=int,
        default=3,
        help="每篇文档最多生成多少个 Golden（默认 3）",
    )
    args = parser.parse_args()

    # ── 扫描文档目录 ──────────────────────────────────────────────
    doc_dir = Path(args.docs_dir)
    if not doc_dir.is_dir():
        print(f"错误：文档目录不存在或不可读：{doc_dir}")
        sys.exit(1)

    doc_files = (
        list(doc_dir.rglob("*.md"))
        + list(doc_dir.rglob("*.txt"))
        + list(doc_dir.rglob("*.pdf"))
    )
    if not doc_files:
        print(f"错误：在 {doc_dir} 中未找到任何 .md/.txt/.pdf 文件")
        sys.exit(1)

    print(f"发现 {len(doc_files)} 个文档文件，开始生成 Golden 数据...")

    # ── 初始化裁判模型与合成器 ─────────────────────────────────────
    from deepeval_metrics import get_judge_model

    judge = get_judge_model()

    from deepeval.synthesizer import Synthesizer

    synth = Synthesizer(model=judge)

    goldens = synth.generate_goldens_from_docs(
        document_paths=[str(f) for f in doc_files],
        max_goldens_per_context=args.limit_per_doc,
    )

    # ── 限制总数 ──────────────────────────────────────────────────
    if args.max_goldens and len(goldens) > args.max_goldens:
        goldens = goldens[:args.max_goldens]

    # ── 保存 ──────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    from deepeval.dataset import EvaluationDataset

    ds = EvaluationDataset(goldens=goldens)

    json_path = ds.save_as("json", str(out_dir), "goldens")
    csv_path = ds.save_as("csv", str(out_dir), "goldens")

    # ── 输出摘要 ──────────────────────────────────────────────────
    print(f"\n生成完成！")
    print(f"  Golden 数量：{len(goldens)}")
    print(f"  JSON 文件：{json_path}")
    print(f"  CSV 文件：{csv_path}")


if __name__ == "__main__":
    main()
