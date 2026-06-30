#!/usr/bin/env python3
"""端到端对话评估脚本。

调用 app 的 HTTP API（POST /agent/chat），走完整链路（RAG → 路由 → 风险检测 → 生成），
对每个模型、每个问题独立发起对话，记录完整回答。

用法:
    # 测试所有模型（单服务器）
    python eval/run_conversation_eval.py --app-url http://localhost:8000

    # 测试单个模型（分布式部署，每台服务器测一个模型）
    python eval/run_conversation_eval.py --app-url http://server1:8000 --models qwen-plus

    # 自定义问题文件和并发数
    python eval/run_conversation_eval.py --app-url http://localhost:8000 --questions eval/questions.md --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """单次 (模型, 问题) 的评估结果。"""

    question_id: str
    model: str
    question: str
    reference: str = ""  # 参考答案/答题点（来自 QA 文件）
    answer: dict[str, Any] = field(default_factory=dict)
    task_type: str = ""
    risk_level: str = ""
    risk_flags: list[str] = field(default_factory=list)
    sources_count: int = 0
    latency_ms: int = 0
    error: str = ""
    # Token 用量
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 流式评估
    ttft_ms: int = 0
    streaming_chunks: list[dict] = field(default_factory=list)


@dataclass
class ModelSummary:
    """单个模型的汇总统计。"""

    model: str
    total: int = 0
    success: int = 0
    errors: int = 0
    avg_latency_ms: float = 0.0
    avg_ttft_ms: float = 0.0
    task_types: dict[str, int] = field(default_factory=dict)
    results: list[EvalResult] = field(default_factory=list)
    # Token 汇总
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_models(models_path: str | Path | None = None) -> list[str]:
    """从 models.json 加载所有模型名列表。"""
    if models_path is None:
        models_path = Path(__file__).resolve().parents[1] / "models.json"
    else:
        models_path = Path(models_path)

    if not models_path.exists():
        print(f"[WARN] models.json not found at {models_path}", file=sys.stderr)
        return []

    with open(models_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    models = list(data.get("models", {}).keys())
    print(f"[INFO] Loaded {len(models)} models from models.json: {models}")
    return models


# ---------------------------------------------------------------------------
# 问题解析
# ---------------------------------------------------------------------------


def parse_questions_markdown(path: str | Path) -> list[dict[str, str]]:
    """解析 Markdown 问题文件。

    支持两种格式：
    1. 纯问题：``## Qxx`` 后跟问题文本
    2. QA 配对：``## Qxx`` + ``## Axx`` 交替出现，Axx 作为参考答案
    """
    path = Path(path)
    if not path.exists():
        print(f"[ERROR] Questions file not found: {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    # 匹配所有 ## (Q|A)xx 块
    pattern = r"##\s+([QA]\d+)\s*\n+(.+?)(?=\n##\s+[QA]\d+|\Z)"
    matches = re.findall(pattern, text, re.DOTALL)

    # 分离 Q 和 A
    q_blocks: dict[str, str] = {}
    a_blocks: dict[str, str] = {}
    for tag, body in matches:
        body = body.strip()
        if tag.startswith("Q") and body:
            q_blocks[tag] = body
        elif tag.startswith("A") and body:
            a_blocks[tag] = body

    questions = []
    for qid in sorted(q_blocks.keys()):
        ref_id = "A" + qid[1:]  # Q01 → A01
        questions.append({
            "id": qid,
            "text": q_blocks[qid],
            "reference": a_blocks.get(ref_id, ""),
        })

    has_refs = any(q.get("reference") for q in questions)
    print(f"[INFO] Parsed {len(questions)} questions from {path}" +
          (f" (with reference answers)" if has_refs else ""))
    return questions


def parse_questions_json(path: str | Path) -> list[dict[str, str]]:
    """解析 ground_truth JSON 文件，提取问题。"""
    path = Path(path)
    if not path.exists():
        print(f"[ERROR] Questions file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = []
    for i, item in enumerate(data.get("questions", []), 1):
        qid = f"Q{i:02d}"
        questions.append({
            "id": qid,
            "text": item.get("q", ""),
            "reference": item.get("expected_keywords", ""),
        })

    print(f"[INFO] Parsed {len(questions)} questions from {path}")
    return questions


def parse_questions(path: str | Path) -> list[dict[str, str]]:
    """自动检测格式并解析问题文件。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".md":
        return parse_questions_markdown(path)
    elif suffix == ".json":
        return parse_questions_json(path)
    else:
        # 尝试 markdown
        return parse_questions_markdown(path)


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------


async def ask_question(
    client: httpx.AsyncClient,
    app_url: str,
    model: str,
    question_id: str,
    question_text: str,
    reference: str,
    tenant_id: str,
    semaphore: asyncio.Semaphore,
    timeout: float = 120.0,
    streaming: bool = False,
) -> EvalResult:
    """向 app API 发送一个问题，返回评估结果。

    Args:
        streaming: 为 True 时走 /eval/streaming-chat 端点（模拟钉钉流式管线），
                   并捕获 TTFT。
    """
    t_start = time.monotonic()
    result = EvalResult(question_id=question_id, model=model, question=question_text, reference=reference)

    endpoint = "/eval/streaming-chat" if streaming else "/agent/chat"
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "user_id": "eval-bot",
        "message": question_text,
        "channel": "eval_streaming" if streaming else "eval",
    }
    # 标准端点支持 model 参数
    if not streaming:
        payload["model"] = model

    async with semaphore:
        try:
            resp = await client.post(
                f"{app_url.rstrip('/')}{endpoint}",
                json=payload,
                timeout=timeout,
            )
            latency = int((time.monotonic() - t_start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                result.answer = data.get("answer", {})
                result.task_type = data.get("task_type", "")
                result.latency_ms = latency

                risk = data.get("risk", {})
                result.risk_level = risk.get("level", "none")
                result.risk_flags = risk.get("flags", [])

                sources = data.get("sources", [])
                result.sources_count = len(sources)

                # Token 用量
                usage = (data.get("debug") or {}).get("usage", {})
                result.prompt_tokens = usage.get("prompt_tokens", 0)
                result.completion_tokens = usage.get("completion_tokens", 0)
                result.total_tokens = usage.get("total_tokens", 0)

                # 流式指标
                retrieval_info = (data.get("debug") or {}).get("retrieval_info", {})
                result.ttft_ms = retrieval_info.get("ttft_ms", 0)
                result.streaming_chunks = retrieval_info.get("streaming_chunks", [])
            else:
                result.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                result.latency_ms = latency
        except httpx.TimeoutException:
            result.error = f"Timeout after {timeout}s"
            result.latency_ms = int((time.monotonic() - t_start) * 1000)
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            result.latency_ms = int((time.monotonic() - t_start) * 1000)

    return result


# ---------------------------------------------------------------------------
# 主评估流程
# ---------------------------------------------------------------------------


async def run_eval(
    app_url: str,
    models: list[str],
    questions: list[dict[str, str]],
    tenant_id: str,
    concurrency: int = 3,
    streaming: bool = False,
) -> dict[str, ModelSummary]:
    """对每个 (模型, 问题) 组合执行评估。"""
    summaries: dict[str, ModelSummary] = {
        m: ModelSummary(model=m) for m in models
    }

    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        for model in models:
            print(f"\n{'='*60}")
            print(f"[EVAL] Testing model: {model} ({len(questions)} questions)")
            print(f"{'='*60}")

            summary = summaries[model]
            tasks = [
                ask_question(client, app_url, model, q["id"], q["text"], q.get("reference", ""), tenant_id, semaphore, streaming=streaming)
                for q in questions
            ]

            # 逐个完成并报告进度
            for i, coro in enumerate(asyncio.as_completed(tasks), 1):
                r = await coro
                summary.total += 1
                if r.error:
                    summary.errors += 1
                    status = "FAIL"
                else:
                    summary.success += 1
                    status = "OK"
                    summary.task_types[r.task_type] = summary.task_types.get(r.task_type, 0) + 1
                    summary.total_prompt_tokens += r.prompt_tokens
                    summary.total_completion_tokens += r.completion_tokens
                    summary.total_tokens += r.total_tokens

                summary.results.append(r)
                tok = f"tok={r.total_tokens}" if r.total_tokens else ""
                ttft = f"ttft={r.ttft_ms}ms" if r.ttft_ms else ""
                print(
                    f"  [{i:3d}/{len(questions)}] {r.question_id} {status} "
                    f"task={r.task_type:25s} latency={r.latency_ms:5d}ms {ttft} {tok} "
                    f"{'ERROR: ' + r.error if r.error else ''}"
                )

            # 计算平均延迟 + TTFT
            latencies = [r.latency_ms for r in summary.results if not r.error]
            if latencies:
                summary.avg_latency_ms = sum(latencies) / len(latencies)
            ttfts = [r.ttft_ms for r in summary.results if not r.error and r.ttft_ms > 0]
            if ttfts:
                summary.avg_ttft_ms = sum(ttfts) / len(ttfts)

    return summaries


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------


def _answer_text(answer: dict[str, Any] | str) -> str:
    """Extract full text from answer dict (sections 优先，summary 是其精简版避免重复)."""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        parts = []
        sections = answer.get("sections", [])
        if sections:
            for sec in sections:
                if isinstance(sec, dict):
                    content = sec.get("content", "")
                    if content:
                        parts.append(content)
        else:
            s = answer.get("summary", "")
            if s:
                parts.append(s)
        return "\n\n".join(parts)
    return str(answer) if answer else ""


def write_csv_report(
    summaries: dict[str, ModelSummary],
    questions: list[dict[str, str]],
    output_path: Path,
) -> None:
    """写入 CSV 格式报告（不含截断，完整回答）。"""
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)

    models = list(summaries.keys())
    has_ref = any(r.reference for s in summaries.values() for r in s.results)
    header = ["question_id", "question"]
    if has_ref:
        header.append("reference")
    for m in models:
        header += [
            f"{m}_task_type", f"{m}_risk_level", f"{m}_latency_ms", f"{m}_ttft_ms",
            f"{m}_prompt_tokens", f"{m}_completion_tokens", f"{m}_total_tokens",
            f"{m}_answer",
        ]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)

        for q in questions:
            row = [q["id"], q["text"]]
            if has_ref:
                row.append(q.get("reference", ""))
            for m in models:
                r = next((x for x in summaries[m].results if x.question_id == q["id"]), None)
                if r is None:
                    row += ["", "", 0, 0, 0, 0, 0, ""]
                else:
                    row += [
                        r.task_type, r.risk_level, r.latency_ms, r.ttft_ms,
                        r.prompt_tokens, r.completion_tokens, r.total_tokens,
                        _answer_text(r.answer),
                    ]
            w.writerow(row)

    print(f"[CSV] Report saved: {output_path}")


def write_json_report(
    summaries: dict[str, ModelSummary],
    questions: list[dict[str, str]],
    app_url: str,
    output_path: Path,
) -> None:
    """写入 JSON 格式的详细报告。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app_url": app_url,
            "total_questions": len(questions),
            "models_tested": list(summaries.keys()),
        },
        "summaries": {
            name: {
                "model": s.model,
                "total": s.total,
                "success": s.success,
                "errors": s.errors,
                "avg_latency_ms": round(s.avg_latency_ms, 1),
                "avg_ttft_ms": round(s.avg_ttft_ms, 1),
                "task_types": s.task_types,
                "total_prompt_tokens": s.total_prompt_tokens,
                "total_completion_tokens": s.total_completion_tokens,
                "total_tokens": s.total_tokens,
            }
            for name, s in summaries.items()
        },
        "details": [
            {
                "question_id": r.question_id,
                "model": r.model,
                "question": r.question,
                "reference": r.reference,
                "task_type": r.task_type,
                "answer": r.answer,
                "risk_level": r.risk_level,
                "risk_flags": r.risk_flags,
                "sources_count": r.sources_count,
                "latency_ms": r.latency_ms,
                "ttft_ms": r.ttft_ms,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "error": r.error,
            }
            for s in summaries.values()
            for r in s.results
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n[JSON] Report saved: {output_path}")


def write_markdown_report(
    summaries: dict[str, ModelSummary],
    questions: list[dict[str, str]],
    app_url: str,
    output_path: Path,
) -> None:
    """写入 Markdown 格式的可读报告。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"# Sales Agent 评估报告")
    lines.append(f"")
    lines.append(f"**时间**: {ts}  ")
    lines.append(f"**App URL**: {app_url}  ")
    lines.append(f"**问题数**: {len(questions)}  ")
    lines.append(f"**测试模型数**: {len(summaries)}  ")
    lines.append(f"")

    # 汇总表
    lines.append("## 模型汇总")
    lines.append("")
    lines.append("| 模型 | 总数 | 成功 | 失败 | 成功率 | 平均延迟 | 平均TTFT | Token(输入/输出/总计) |")
    lines.append("|------|------|------|------|--------|----------|----------|----------------------|")
    for s in summaries.values():
        rate = f"{s.success / s.total * 100:.1f}%" if s.total > 0 else "N/A"
        tok_str = f"{s.total_prompt_tokens}/{s.total_completion_tokens}/{s.total_tokens}"
        lines.append(
            f"| {s.model} | {s.total} | {s.success} | {s.errors} "
            f"| {rate} | {s.avg_latency_ms:.0f}ms | {s.avg_ttft_ms:.0f}ms | {tok_str} |"
        )
    lines.append("")

    # 任务类型分布
    lines.append("## 任务类型分布")
    lines.append("")
    all_types: set[str] = set()
    for s in summaries.values():
        all_types.update(s.task_types.keys())
    all_types_sorted = sorted(all_types)

    header = "| 模型 |" + "|".join(f" {t} " for t in all_types_sorted) + "|"
    lines.append(header)
    lines.append("|------|" + "|".join("------" for _ in all_types_sorted) + "|")
    for s in summaries.values():
        counts = "|".join(str(s.task_types.get(t, 0)) for t in all_types_sorted)
        lines.append(f"| {s.model} |{counts}|")
    lines.append("")

    # 逐题对比
    lines.append("## 逐题对比")
    lines.append("")

    for q in questions:
        lines.append(f"### {q['id']}")
        lines.append(f"")
        lines.append(f"> {q['text']}")
        ref = q.get("reference", "")
        if ref:
            lines.append(f"")
            lines.append(f"**参考要点**: {ref}")
        lines.append(f"")

        for s in summaries.values():
            r = next((x for x in s.results if x.question_id == q["id"]), None)
            if r is None:
                lines.append(f"**{s.model}**: (未执行)")
                lines.append("")
                continue

            if r.error:
                lines.append(f"**{s.model}** ❌ ERROR: {r.error}")
            else:
                lines.append(
                    f"**{s.model}** — task={r.task_type}, "
                    f"risk={r.risk_level}, sources={r.sources_count}, "
                    f"latency={r.latency_ms}ms, ttft={r.ttft_ms}ms, "
                    f"tokens={r.prompt_tokens}/{r.completion_tokens}/{r.total_tokens}"
                )

                answer = r.answer
                if isinstance(answer, dict):
                    sections = answer.get("sections", [])
                    if sections:
                        # 有 sections 时跳过 summary（summary 是其精简版，避免重复）
                        for sec in sections:
                            if isinstance(sec, dict):
                                title = sec.get("title", "")
                                content = sec.get("content", "")
                                if title:
                                    lines.append(f"")
                                    lines.append(f"**{title}**")
                                if content:
                                    lines.append(content)
                    else:
                        summary_text = answer.get("summary", "")
                        if summary_text:
                            lines.append(f"")
                            lines.append(summary_text)
                elif isinstance(answer, str):
                    lines.append(answer)
            lines.append("")

    # 错误汇总
    all_errors = [(s.model, r) for s in summaries.values() for r in s.results if r.error]
    if all_errors:
        lines.append("## 错误详情")
        lines.append("")
        for model, r in all_errors:
            lines.append(f"- **{model}** / {r.question_id}: {r.error}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[MD] Report saved: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sales Agent 端到端对话评估",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app-url", required=True,
        help="App API 地址，如 http://localhost:8000",
    )
    parser.add_argument(
        "--models", default="all",
        help="要测试的模型名（逗号分隔），默认 all = models.json 中所有模型",
    )
    parser.add_argument(
        "--questions", default=None,
        help="问题文件路径（支持 .md / .json），默认 eval/questions.md",
    )
    parser.add_argument(
        "--tenant-id", default="taishan",
        help="租户 ID（默认 taishan）",
    )
    parser.add_argument(
        "--concurrency", type=int, default=3,
        help="并发请求数（默认 3）",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="输出目录（默认 eval/results）",
    )
    parser.add_argument(
        "--stream", action="store_true",
        help="走钉钉流式管线（/eval/streaming-chat），记录 TTFT",
    )
    args = parser.parse_args()

    # 解析模型列表
    if args.models == "all":
        models = load_models()
        if not models:
            print("[ERROR] No models found in models.json", file=sys.stderr)
            sys.exit(1)
    else:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    print(f"[INFO] Models to test: {models}")

    # 解析问题文件
    questions_path = args.questions
    if questions_path is None:
        default_md = Path(__file__).resolve().parent / "questions.md"
        if default_md.exists():
            questions_path = default_md
        else:
            questions_path = Path(__file__).resolve().parent / "ground_truth_30q.json"

    questions = parse_questions(questions_path)
    if not questions:
        print("[ERROR] No questions found", file=sys.stderr)
        sys.exit(1)

    # 输出目录
    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 运行评估
    print(f"\n[START] Evaluating {len(models)} model(s) x {len(questions)} questions")
    print(f"[START] App URL: {args.app_url}")
    print(f"[START] Streaming: {args.stream}")
    print(f"[START] Concurrency: {args.concurrency}")
    t0 = time.monotonic()

    summaries = asyncio.run(
        run_eval(
            app_url=args.app_url,
            models=models,
            questions=questions,
            tenant_id=args.tenant_id,
            concurrency=args.concurrency,
            streaming=args.stream,
        )
    )

    elapsed = time.monotonic() - t0
    print(f"\n[DONE] Completed in {elapsed:.1f}s")

    # 输出报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"eval_{timestamp}.json"
    md_path = output_dir / f"eval_{timestamp}.md"

    write_json_report(summaries, questions, args.app_url, json_path)
    write_markdown_report(summaries, questions, args.app_url, md_path)
    write_csv_report(summaries, questions, output_dir / f"eval_{timestamp}.csv")

    # 打印汇总
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for s in summaries.values():
        rate = f"{s.success / s.total * 100:.1f}%" if s.total > 0 else "N/A"
        print(
            f"  {s.model:20s}  success={s.success}/{s.total} ({rate})  "
            f"avg_latency={s.avg_latency_ms:.0f}ms  avg_ttft={s.avg_ttft_ms:.0f}ms  errors={s.errors}  "
            f"tokens={s.total_tokens} (in:{s.total_prompt_tokens} out:{s.total_completion_tokens})"
        )


if __name__ == "__main__":
    main()
