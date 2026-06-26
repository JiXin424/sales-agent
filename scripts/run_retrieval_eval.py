#!/usr/bin/env python3
"""检索召回率评测脚本：30 题明确版 + trace 落盘 + round 报告。

对标 Ontology-Taishan ``scripts/v4_eval_30q.py`` 的评测方法论，适配
本项目的 pgvector + 关键词双通道检索架构。

指标：
- recall_by_keyword@k：预期关键词在 top-k 结果文本中的命中率
- MRR：第一个预期关键词命中的排名的倒数均值
- 按类别汇总召回率
- 零召回问题清单

用法：
    # 纯向量检索评测
    python scripts/run_retrieval_eval.py --tenant taishan --mode vector --round 01

    # 纯关键词检索评测
    python scripts/run_retrieval_eval.py --tenant taishan --mode keyword --round 01

    # 混合检索评测
    python scripts/run_retrieval_eval.py --tenant taishan --mode hybrid --round 01

    # 不落 trace（更快）
    python scripts/run_retrieval_eval.py --tenant taishan --mode hybrid --round 01 --no-trace
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


# ── 数据类 ─────────────────────────────────────────────────────────────


@dataclass
class EvalDetail:
    """单题评测详情。"""

    qid: str = ""
    query: str = ""
    category: str = ""
    note: str = ""
    expected_keywords: list[str] = field(default_factory=list)
    top_k: int = 5
    top_texts: list[str] = field(default_factory=list)
    top_scores: list[float] = field(default_factory=list)
    top_titles: list[str] = field(default_factory=list)
    hit_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    recall: float = 0.0
    mrr: float = 0.0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvalSummary:
    """评测汇总。"""

    round_id: str = ""
    mode: str = ""
    timestamp: str = ""
    total_queries: int = 0
    top_k: int = 5
    overall_recall: float = 0.0
    overall_mrr: float = 0.0
    full_hit_queries: int = 0
    zero_recall_queries: list[str] = field(default_factory=list)
    by_category: dict[str, dict] = field(default_factory=dict)
    total_keywords: int = 0
    total_hit_keywords: int = 0
    avg_latency_ms: float = 0.0


# ── 关联函数：获取检索器 ──────────────────────────────────────────────


async def _get_retriever(tenant_id: str, mode: str):
    """初始化指定模式的检索器。"""
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, get_session_factory
    from sales_agent.services.tenant_resolver import TenantResolver

    settings = get_settings()
    await init_db()

    factory = get_session_factory()
    db = factory()

    resolver = TenantResolver(db)
    tenant_info = await resolver.resolve(tenant_id)
    provider = resolver.get_model_provider(tenant_info)

    if mode == "vector":
        from sales_agent.services.retriever import Retriever
        return db, Retriever(db, provider.embedding), None
    elif mode == "keyword":
        from sales_agent.rag.keyword_retriever import KeywordRetriever
        kr = KeywordRetriever(db)
        await kr.build_index(tenant_id)
        return db, None, kr
    elif mode == "hybrid":
        from sales_agent.services.retriever import HybridRetriever
        from sales_agent.rag.keyword_retriever import KeywordRetriever
        kr = KeywordRetriever(db)
        await kr.build_index(tenant_id)
        hr = HybridRetriever(
            vector_retriever=Retriever(db, provider.embedding),
            keyword_retriever=kr,
        )
        return db, hr, kr
    else:
        raise ValueError(f"Unknown mode: {mode}")


# ── 评测核心 ───────────────────────────────────────────────────────────


def _compute_recall_mrr(
    expected: list[str],
    top_texts: list[str],
    top_k: int,
) -> tuple[float, float, list[str], list[str]]:
    """计算单题的 recall 和 MRR。

    Returns:
        (recall, mrr, hit_keywords, missing_keywords)
    """
    if not expected:
        return 1.0, 0.0, [], []

    hit_keywords: list[str] = []
    missing_keywords: list[str] = []
    first_rank = top_k + 1  # 用于 MRR

    for kw in expected:
        kw_lower = kw.lower()
        found = False
        for rank, text in enumerate(top_texts[:top_k], start=1):
            if kw_lower in text.lower():
                hit_keywords.append(kw)
                if rank < first_rank:
                    first_rank = rank
                found = True
                break
        if not found:
            missing_keywords.append(kw)

    recall = len(hit_keywords) / len(expected) if expected else 1.0
    mrr = 1.0 / first_rank if first_rank <= top_k else 0.0
    return recall, mrr, hit_keywords, missing_keywords


async def _run_vector_search(retriever, tenant_id: str, query: str, top_k: int) -> tuple[list[str], list[float], list[str], float]:
    """执行纯向量检索。"""
    start = time.monotonic()
    result = await retriever.retrieve(tenant_id, query, top_k=top_k)
    latency = (time.monotonic() - start) * 1000
    texts = [s.text for s in result.sources]
    scores = [s.score for s in result.sources]
    titles = [s.title for s in result.sources]
    return texts, scores, titles, latency


async def _run_keyword_search(kr, tenant_id: str, query: str, top_k: int) -> tuple[list[str], list[float], list[str], float]:
    """执行纯关键词检索。"""
    start = time.monotonic()
    hits = await kr.search(tenant_id, query, top_k=top_k)
    latency = (time.monotonic() - start) * 1000
    texts = [h.text for h in hits]
    scores = [h.score for h in hits]
    titles = [h.title for h in hits]
    return texts, scores, titles, latency


async def _run_hybrid_search(hr, tenant_id: str, query: str, top_k: int) -> tuple[list[str], list[float], list[str], float]:
    """执行混合检索（向量 + 关键词 RRF 融合）。"""
    start = time.monotonic()
    result = await hr.retrieve(tenant_id, query, top_k=top_k)
    latency = (time.monotonic() - start) * 1000
    texts = [s.text for s in result.sources]
    scores = [s.score for s in result.sources]
    titles = [s.title for s in result.sources]
    return texts, scores, titles, latency


async def run_eval(
    tenant_id: str,
    mode: str,
    ground_truth_path: str | Path,
    top_k: int = 5,
    save_traces: bool = True,
    traces_dir: str | Path | None = None,
) -> dict:
    """执行完整评测。

    Returns:
        {"summary": EvalSummary, "details": list[EvalDetail]}
    """
    # 加载 ground truth
    gt_path = Path(ground_truth_path)
    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)
    questions = gt_data.get("questions", [])

    # 初始化检索器
    db, retriever, kr = await _get_retriever(tenant_id, mode)

    details: list[EvalDetail] = []
    traces_list: list[dict] = []

    try:
        for i, item in enumerate(questions, start=1):
            qid = f"Q{i:02d}"
            query = item["q"]
            expected = item.get("expected_keywords", [])

            # 执行检索
            if mode == "vector":
                texts, scores, titles, latency = await _run_vector_search(
                    retriever, tenant_id, query, top_k,
                )
            elif mode == "keyword":
                texts, scores, titles, latency = await _run_keyword_search(
                    kr, tenant_id, query, top_k,
                )
            elif mode == "hybrid":
                texts, scores, titles, latency = await _run_hybrid_search(
                    retriever, tenant_id, query, top_k,
                )
            else:
                raise ValueError(f"Unknown mode: {mode}")

            # 计算指标
            recall, mrr, hit_kw, missing_kw = _compute_recall_mrr(expected, texts, top_k)

            detail = EvalDetail(
                qid=qid,
                query=query,
                category=item.get("category", ""),
                note=item.get("note", ""),
                expected_keywords=expected,
                top_k=top_k,
                top_texts=texts,
                top_scores=scores,
                top_titles=titles,
                hit_keywords=hit_kw,
                missing_keywords=missing_kw,
                recall=recall,
                mrr=mrr,
                latency_ms=latency,
            )
            details.append(detail)

            # 生成 trace
            trace = {
                "qid": qid,
                "query": query,
                "category": item.get("category", ""),
                "expected_keywords": expected,
                "hit_keywords": hit_kw,
                "missing_keywords": missing_kw,
                "recall": recall,
                "mrr": mrr,
                "latency_ms": latency,
                "top_results": [
                    {"rank": r, "title": t, "score": s, "text_preview": txt[:200]}
                    for r, (t, s, txt) in enumerate(zip(titles, scores, texts), start=1)
                ],
            }
            traces_list.append(trace)

        # 汇总
        by_category: dict[str, dict] = {}
        for d in details:
            if d.category not in by_category:
                by_category[d.category] = {"recalls": [], "mrrs": [], "count": 0}
            by_category[d.category]["recalls"].append(d.recall)
            by_category[d.category]["mrrs"].append(d.mrr)
            by_category[d.category]["count"] += 1

        recalls = [d.recall for d in details]
        mrrs = [d.mrr for d in details]
        latencies = [d.latency_ms for d in details]

        summary = EvalSummary(
            mode=mode,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            total_queries=len(details),
            top_k=top_k,
            overall_recall=round(sum(recalls) / max(len(recalls), 1), 4),
            overall_mrr=round(sum(mrrs) / max(len(mrrs), 1), 4),
            full_hit_queries=sum(1 for r in recalls if r >= 1.0),
            zero_recall_queries=[d.qid for d in details if d.recall == 0.0],
            by_category={
                c: {
                    "recall": round(sum(v["recalls"]) / max(v["count"], 1), 4),
                    "mrr": round(sum(v["mrrs"]) / max(v["count"], 1), 4),
                    "count": v["count"],
                }
                for c, v in sorted(by_category.items())
            },
            total_keywords=sum(len(d.expected_keywords) for d in details),
            total_hit_keywords=sum(len(d.hit_keywords) for d in details),
            avg_latency_ms=round(sum(latencies) / max(len(latencies), 1), 1),
        )

        # 落 trace
        if save_traces and traces_dir:
            td = Path(traces_dir)
            td.mkdir(parents=True, exist_ok=True)
            for trace in traces_list:
                trace_path = td / f"{trace['qid']}.json"
                with open(trace_path, "w", encoding="utf-8") as f:
                    json.dump(trace, f, ensure_ascii=False, indent=2)

        return {"summary": summary, "details": details, "traces": traces_list}

    finally:
        await db.close()


# ── 报告生成 ───────────────────────────────────────────────────────────


def write_round_report(result: dict, round_id: str, output: Path) -> None:
    """生成 Markdown round 报告。"""
    s = result["summary"]
    details = result["details"]

    lines = [
        f"# 知识库检索评测 Round {round_id.upper()} 报告",
        "",
        f"> 生成时间：{s.timestamp}",
        f"> 检索模式：**{s.mode}**",
        f"> top-k：{s.top_k}",
        f"> 评测集：30 题明确版（eval/ground_truth_30q.json）",
        "",
        "## 整体指标",
        "",
        f"- 整体召回率（keyword recall@k）：**{s.overall_recall:.1%}**",
        f"- 整体 MRR：**{s.overall_mrr:.3f}**",
        f"- 全命中题数：**{s.full_hit_queries}/{s.total_queries}**",
        f"- 零召回题数：**{len(s.zero_recall_queries)}**（{', '.join(s.zero_recall_queries) or '无'}）",
        f"- 关键词总命中率：**{s.total_hit_keywords}/{s.total_keywords}** = **{s.total_hit_keywords/max(s.total_keywords, 1):.1%}**",
        f"- 平均检索耗时：**{s.avg_latency_ms:.0f} ms**",
        "",
        "## 按类别召回率",
        "",
        "| 类别 | Recall | MRR | 题目数 |",
        "|---|---|---|---|",
    ]
    for cat, vals in s.by_category.items():
        lines.append(f"| {cat} | {vals['recall']:.1%} | {vals['mrr']:.3f} | {vals['count']} |")

    lines.extend(["", "## 每题详情", ""])
    for d in details:
        lines.append(f"### {d.qid} [{d.category}] {d.query}")
        lines.append(f"- **考察点**：{d.note}")
        lines.append(f"- **预期关键词**：{', '.join(d.expected_keywords)}")
        lines.append(f"- **命中关键词**：{', '.join(d.hit_keywords) or '无'}")
        lines.append(f"- **缺失关键词**：{', '.join(d.missing_keywords) or '无'}")
        lines.append(f"- **recall**：{d.recall:.0%} | **MRR**：{d.mrr:.3f} | **耗时**：{d.latency_ms:.0f} ms")
        lines.append(f"- **top-{d.top_k} 结果**：")
        for idx, (title, score) in enumerate(zip(d.top_titles, d.top_scores), start=1):
            if idx <= 5:
                preview = d.top_texts[idx - 1][:80].replace("\n", " ")
                lines.append(f"  {idx}. [{score:.3f}] **{title}** — {preview}...")
        if d.error:
            lines.append(f"- **错误**：{d.error}")
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="知识库检索召回率评测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
    python scripts/run_retrieval_eval.py --tenant taishan --mode hybrid --round 01
    python scripts/run_retrieval_eval.py --tenant taishan --mode vector --round 01 --top-k 10
    python scripts/run_retrieval_eval.py --tenant taishan --mode keyword --round 02 --no-trace
""",
    )
    parser.add_argument("--tenant", "-t", required=True, help="租户 ID")
    parser.add_argument(
        "--mode", "-m", default="hybrid",
        choices=["vector", "keyword", "hybrid"],
        help="检索模式（默认 hybrid）",
    )
    parser.add_argument("--round", "-r", required=True, help="Round 编号，如 01、02")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="检索返回数量（默认 5）")
    parser.add_argument(
        "--ground-truth", "-g",
        default=str(Path(__file__).resolve().parent / "ground_truth_30q.json"),
        help="Ground truth JSON 文件路径",
    )
    parser.add_argument("--no-trace", action="store_true", help="不落 trace JSON（更快）")
    parser.add_argument(
        "--output-dir", "-o",
        default=str(Path(__file__).resolve().parents[1] / "eval" / "rounds"),
        help="输出目录",
    )
    args = parser.parse_args()

    # 设置 round_id（可在 summary 中覆盖）
    round_id = args.round

    gt_path = Path(args.ground_truth)
    if not gt_path.exists():
        print(f"❌ Ground truth 文件不存在: {gt_path}")
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_dir = out_dir / "traces" / f"round_{round_id}" if not args.no_trace else None

    print(f"🚀 检索评测 Round {round_id}")
    print(f"   租户: {args.tenant} | 模式: {args.mode} | top-k: {args.top_k}")
    print(f"   Ground truth: {gt_path} ({len(json.loads(gt_path.read_text(encoding='utf-8')).get('questions', []))} 题)")
    print()

    result = asyncio.run(run_eval(
        tenant_id=args.tenant,
        mode=args.mode,
        ground_truth_path=gt_path,
        top_k=args.top_k,
        save_traces=not args.no_trace,
        traces_dir=traces_dir,
    ))

    s = result["summary"]
    round_id = round_id  # keep as-is

    # 输出简短总结
    print(f"✓ Round {round_id.upper()}: "
          f"recall={s.overall_recall:.1%} | "
          f"MRR={s.overall_mrr:.3f} | "
          f"全命中={s.full_hit_queries}/{s.total_queries} | "
          f"耗时={s.avg_latency_ms:.0f}ms")
    for cat, vals in s.by_category.items():
        print(f"  · {cat}: recall={vals['recall']:.1%}")

    if s.zero_recall_queries:
        print(f"  ✗ 零召回 {len(s.zero_recall_queries)} 题：{', '.join(s.zero_recall_queries)}")

    # 生成报告
    report_path = out_dir / f"round_{round_id}_{s.mode}.md"
    write_round_report(result, round_id, report_path)
    print(f"\n✓ 报告已保存：{report_path}")
    if not args.no_trace:
        print(f"✓ Trace 已落到：{traces_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
