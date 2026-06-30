#!/usr/bin/env python3
"""合并多个评估 JSON 结果文件（分布式评估场景）。

用法:
    # 合并两个服务器的结果
    python eval/merge_results.py eval/results/server1_*.json eval/results/server2_*.json -o merged.md

    # 指定输出目录
    python eval/merge_results.py eval/results/*.json -o eval/results/merged_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_result_files(paths: list[str]) -> list[dict]:
    """加载所有 JSON 结果文件。"""
    results = []
    for pattern in paths:
        p = Path(pattern)
        if p.is_file():
            results.append((p, _load_json(p)))
        else:
            # glob 模式
            parent = p.parent if p.parent != Path(".") else Path(".")
            for f in sorted(parent.glob(p.name)):
                results.append((f, _load_json(f)))

    if not results:
        print("[ERROR] No result files found", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Loaded {len(results)} result files")
    return results


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge(results: list[tuple[Path, dict]]) -> dict:
    """合并多个评估结果。"""
    if len(results) == 1:
        return results[0][1]

    merged = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "merged_from": [str(p) for p, _ in results],
            "total_questions": 0,
            "models_tested": [],
        },
        "summaries": {},
        "details": [],
    }

    seen_models: set[str] = set()

    for filepath, data in results:
        # 合并 summaries
        for model_name, summary in data.get("summaries", {}).items():
            if model_name in seen_models:
                print(f"[WARN] Duplicate model {model_name} in {filepath}, skipping")
                continue
            seen_models.add(model_name)
            merged["summaries"][model_name] = summary

        # 合并 details
        merged["details"].extend(data.get("details", []))

        # 更新 meta
        merged["meta"]["total_questions"] = max(
            merged["meta"]["total_questions"],
            data.get("meta", {}).get("total_questions", 0),
        )

    merged["meta"]["models_tested"] = sorted(seen_models)

    print(f"[INFO] Merged: {len(seen_models)} models, {len(merged['details'])} total results")
    return merged


def write_markdown(merged: dict, output_path: Path) -> None:
    """写入合并后的 Markdown 报告。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    meta = merged.get("meta", {})
    ts = meta.get("timestamp", "")

    lines.append("# Sales Agent 评估报告（合并）")
    lines.append("")
    lines.append(f"**时间**: {ts}")
    lines.append(f"**合并来源**: {len(meta.get('merged_from', []))} 个文件")
    lines.append(f"**模型数**: {len(meta.get('models_tested', []))}")
    lines.append(f"**问题数**: {meta.get('total_questions', 0)}")
    lines.append("")

    # 汇总表
    lines.append("## 模型汇总")
    lines.append("")
    lines.append("| 模型 | 总数 | 成功 | 失败 | 成功率 | 平均延迟 | Token(输入/输出/总计) |")
    lines.append("|------|------|------|------|--------|----------|----------------------|")

    summaries = merged.get("summaries", {})
    for name in sorted(summaries.keys()):
        s = summaries[name]
        total = s.get("total", 0)
        success = s.get("success", 0)
        errors = s.get("errors", 0)
        rate = f"{success / total * 100:.1f}%" if total > 0 else "N/A"
        avg_lat = s.get("avg_latency_ms", 0)
        pt = s.get("total_prompt_tokens", 0)
        ct = s.get("total_completion_tokens", 0)
        tt = s.get("total_tokens", 0)
        lines.append(
            f"| {name} | {total} | {success} | {errors} | {rate} | {avg_lat:.0f}ms | {pt}/{ct}/{tt} |"
        )
    lines.append("")

    # 按问题分组对比
    lines.append("## 逐题对比")
    lines.append("")

    details = merged.get("details", [])
    # 按 question_id 分组
    by_question: dict[str, list[dict]] = {}
    for d in details:
        qid = d.get("question_id", "?")
        by_question.setdefault(qid, []).append(d)

    for qid in sorted(by_question.keys()):
        items = by_question[qid]
        question_text = items[0].get("question", "") if items else ""
        lines.append(f"### {qid}")
        lines.append(f"")
        lines.append(f"> {question_text}")
        lines.append(f"")

        for item in items:
            model = item.get("model", "?")
            error = item.get("error", "")
            if error:
                lines.append(f"**{model}** ❌ {error}")
            else:
                task = item.get("task_type", "?")
                risk = item.get("risk_level", "none")
                sources = item.get("sources_count", 0)
                latency = item.get("latency_ms", 0)
                pt = item.get("prompt_tokens", 0)
                ct = item.get("completion_tokens", 0)
                tt = item.get("total_tokens", 0)
                lines.append(
                    f"**{model}** — task={task}, risk={risk}, "
                    f"sources={sources}, latency={latency}ms, "
                    f"tokens={pt}/{ct}/{tt}"
                )

                answer = item.get("answer", {})
                if isinstance(answer, dict):
                    sections = answer.get("sections", [])
                    if sections:
                        for sec in sections:
                            if isinstance(sec, dict):
                                content = sec.get("content", "")
                                if content:
                                    lines.append(f"")
                                    lines.append(content)
                    else:
                        summary_text = answer.get("summary", "")
                        if summary_text:
                            lines.append(f"")
                            lines.append(summary_text)
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[MD] Merged report saved: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="合并多个评估 JSON 结果文件")
    parser.add_argument("files", nargs="+", help="JSON 结果文件路径（支持 glob）")
    parser.add_argument("-o", "--output", default=None, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    results = load_result_files(args.files)
    merged = merge(results)

    output = Path(args.output) if args.output else Path("eval/results/merged_report.md")
    write_markdown(merged, output)


if __name__ == "__main__":
    main()
